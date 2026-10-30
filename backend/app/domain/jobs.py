from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
TRACKING_PREFIXES = ("utm_",)


def normalize_text(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"\s+", " ", value).strip()


def canonical_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("job URL must be an absolute HTTP(S) URL")
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    port = parsed.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k.casefold() not in TRACKING_KEYS
             and not any(k.casefold().startswith(p) for p in TRACKING_PREFIXES)]
    return urlunsplit(("https", netloc, parsed.path.rstrip("/") or "/", urlencode(pairs), ""))


def fingerprint(company: str, title: str, location: str, description: str) -> str:
    canonical = "\x1f".join(map(normalize_text, (company, title, location, description)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_TARGET_TITLE = re.compile(
    r"\b(help\s*desk|service\s*desk|it support|technical support|product support analyst|desktop support|"
    r"field service (?:technician|support)|field (?:service|support) technician|"
    r"endpoint support|systems? support|it technician|network technician|"
    r"noc (?:technician|analyst)|cloud support|microsoft 365 support|"
    r"systems? administrator|infrastructure (?:support|technician)|operations support)\b", re.I)
_EXCLUDED_TITLE = re.compile(r"\b(senior|sr\.?|lead|principal|architect|manager|director|head|vp)\b", re.I)
_PROVINCE_MARKERS = {
    "Ontario": re.compile(r"\b(ontario|ON|Toronto|Ottawa|Kitchener|Waterloo|Hamilton|Mississauga|"
                          r"Brampton|Markham|London|Windsor|Kingston|Guelph|Barrie|Sudbury)\b", re.I),
    "Alberta": re.compile(r"\b(alberta|AB|Calgary|Edmonton|Red Deer|Lethbridge)\b", re.I),
    "Manitoba": re.compile(r"\b(manitoba|MB|Winnipeg|Brandon)\b", re.I),
}


def target_job_scope(title: str, location: str, work_mode: str | None = None) -> dict[str, str] | None:
    """Keep only requested support/infrastructure roles and target Canadian regions."""
    if not isinstance(title, str) or not _TARGET_TITLE.search(title) or _EXCLUDED_TITLE.search(title):
        return None
    place = f"{location or ''} {work_mode or ''}".strip()
    province = next((name for name, marker in _PROVINCE_MARKERS.items() if marker.search(place)), None)
    remote_canada = bool(re.search(r"remote.{0,30}canada|canada.{0,30}remote", place, re.I))
    if not province and not remote_canada:
        return None
    lowered = place.casefold()
    mode = next((value for marker, value in (("remote", "Remote"), ("hybrid", "Hybrid"),
                 ("on-site", "On-site"), ("onsite", "On-site"), ("field", "Field / Multi-site"))
                if marker in lowered), "Unspecified")
    category = ("L1 / Help Desk" if re.search(r"help\s*desk|service\s*desk|it support|support analyst", title, re.I)
                else "L2 / Desktop / Technical" if re.search(r"desktop|technical support|field|endpoint", title, re.I)
                else "Systems / Infrastructure" if re.search(r"system|infrastructure|network|noc", title, re.I)
                else "Cloud-oriented")
    return {"province": province or "Remote Canada", "work_mode": mode, "category": category}


@dataclass(frozen=True)
class NormalizedJob:
    company: str
    title: str
    location: str
    province: str | None
    work_mode: str | None
    canonical_url: str
    external_id: str | None
    fingerprint: str
    description: str
    source: str


def normalize_job(raw: dict) -> NormalizedJob:
    required = ("company", "title", "url", "source")
    if any(not isinstance(raw.get(k), str) or not raw[k].strip() for k in required):
        raise ValueError("company, title, URL, and source are required")
    if len(raw.get("description", "")) > 120_000:
        raise ValueError("description exceeds 120000 characters")
    company = re.sub(r"\s+", " ", raw["company"]).strip()[:200]
    title = re.sub(r"\s+", " ", raw["title"]).strip()[:300]
    location = re.sub(r"\s+", " ", str(raw.get("location", "Unspecified"))).strip()[:300]
    description = str(raw.get("description", ""))
    return NormalizedJob(company, title, location, raw.get("province"), raw.get("work_mode"),
                         canonical_url(raw["url"]), raw.get("external_id"),
                         fingerprint(company, title, location, description), description,
                         raw["source"].strip()[:80])
