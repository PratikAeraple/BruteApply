from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import json
import re
from datetime import datetime, timezone

import httpx


MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_JOBS = 250
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
TAG = re.compile(r"<[^>]*>")


def _plain(value: str | None, limit: int = 120_000) -> str:
    text = unescape(TAG.sub(" ", value or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


@dataclass(frozen=True)
class PublicBoard:
    name: str
    host: str
    slug: str
    company_name: str | None = None

    def url(self) -> str:
        if not SLUG.fullmatch(self.slug):
            raise ValueError("board identifier has an invalid format")
        if self.name == "greenhouse" and self.host == "boards-api.greenhouse.io":
            return f"https://boards-api.greenhouse.io/v1/boards/{self.slug}/jobs?content=true"
        if self.name == "lever" and self.host == "api.lever.co":
            return f"https://api.lever.co/v0/postings/{self.slug}?mode=json&limit=100"
        raise ValueError("provider host is not an approved public ATS endpoint")


class PublicATSProvider:
    """Fetch only an owner-configured public Greenhouse or Lever board."""

    def __init__(self, board: PublicBoard, *, transport: httpx.AsyncBaseTransport | None = None):
        if board.name not in {"greenhouse", "lever"}:
            raise ValueError("unsupported provider")
        # Calling url() validates both the provider and user-supplied board slug.
        board.url()
        self.board = board
        self.transport = transport

    async def discover(self, client: httpx.AsyncClient | None = None) -> list[dict]:
        own_client = client is None
        request_client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=4.0),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            transport=self.transport,
        )
        try:
            async with request_client.stream(
                "GET", self.board.url(),
                headers={"Accept":"application/json","User-Agent":"BruteApply/0.1 public job-board reader"},
            ) as response:
                if response.url.scheme != "https" or response.url.host != self.board.host:
                    raise ValueError("provider response origin changed")
                response.raise_for_status()
                chunks=[]; size=0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ValueError("provider response exceeds the configured limit")
                    chunks.append(chunk)
            payload = json.loads(b"".join(chunks))
            entries = payload.get("jobs", []) if self.board.name == "greenhouse" else payload
            if not isinstance(entries, list):
                raise ValueError("provider response shape is invalid")
            return [self._normalize(item) for item in entries[:MAX_JOBS] if isinstance(item, dict)]
        finally:
            if own_client:
                await request_client.aclose()

    def _normalize(self, item: dict) -> dict:
        if self.board.name == "greenhouse":
            location = (item.get("location") or {}).get("name") or "Unspecified"
            url = item.get("absolute_url")
            external_id = str(item.get("id", "")) or None
            title = item.get("title", "")
            description = _plain(item.get("content"))
            work_mode = None
            employment_type = None
            salary = None
            posted_at = item.get("first_published") or item.get("updated_at")
        else:
            categories = item.get("categories") or {}
            location = categories.get("location") or ", ".join(categories.get("allLocations") or []) or "Unspecified"
            url = item.get("hostedUrl")
            external_id = str(item.get("id", "")) or None
            title = item.get("text", "")
            description = _plain(item.get("descriptionPlain") or item.get("description"))
            work_mode = (item.get("workplaceType") or "").replace("on-site", "On-site").replace("hybrid", "Hybrid").replace("remote", "Remote") or None
            employment_type = (item.get("categories") or {}).get("commitment")
            salary = item.get("salaryRange")
            created_at = item.get("createdAt")
            try:
                posted_at = datetime.fromtimestamp(int(created_at) / 1000, timezone.utc).isoformat() if created_at else None
            except (TypeError, ValueError, OverflowError, OSError):
                posted_at = None
        if not isinstance(url,str) or not url.startswith("https://"):
            raise ValueError("provider returned a non-HTTPS posting URL")
        original_location = _plain(str(location),300)
        normalized_title = _plain(str(title),300)
        # Some ATS boards expose a continent-wide remote label even when the
        # posting itself explicitly restricts applicants to Canada. Preserve
        # the ATS value for evidence and use the narrower eligible region for
        # matching only when the posting says Canada and Remote.
        if ("remote" in original_location.casefold()
                and "canada" in (normalized_title + " " + description).casefold()):
            location = "Remote Canada"
        return {
            "company": self.board.company_name or self.board.slug,
            "title": normalized_title,
            "url": url[:2000],
            "source": self.board.name,
            "description": description,
            "location": _plain(str(location),300),
            "source_location": original_location,
            "posted_at": posted_at,
            "external_id": external_id,
            "work_mode": work_mode,
            "employment_type": employment_type,
            "salary": salary,
            "untrusted": True,
        }
