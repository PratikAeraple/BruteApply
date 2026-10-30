from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import asyncio
import json
from pathlib import Path
import stat
import re
import threading
from typing import Any, Protocol

import httpx


class AIProvider(Protocol):
    async def complete(self, *, system_policy: str, untrusted_input: dict[str, Any],
                       schema: dict[str, Any], task_class: str) -> "AIResult": ...


@dataclass(frozen=True)
class AIResult:
    data: dict[str, Any]
    input_tokens: int
    output_tokens: int
    model: str


class UsageBudget:
    """Process-local test/development guard; production callers must persist reservations in PostgreSQL."""
    def __init__(self, *, daily_input_limit: int = 0, monthly_input_limit: int = 0,
                 daily_output_limit: int = 0, monthly_output_limit: int = 0):
        self.limits={"daily_input":daily_input_limit,"monthly_input":monthly_input_limit,
                     "daily_output":daily_output_limit,"monthly_output":monthly_output_limit}
        self._usage: dict[tuple[str, date], list[int]]={}
        self._lock=threading.Lock()

    def reserve(self, input_tokens: int, output_tokens: int, *, today: date | None = None) -> None:
        when=today or datetime.now(timezone.utc).date()
        with self._lock:
            day=self._usage.setdefault(("day",when),[0,0]); month=self._usage.setdefault(("month",when.replace(day=1)),[0,0])
            if not self.limits["daily_input"] or not self.limits["monthly_input"] or not self.limits["daily_output"] or not self.limits["monthly_output"]:
                raise RuntimeError("AI usage budget is not configured")
            if day[0]+input_tokens>self.limits["daily_input"] or month[0]+input_tokens>self.limits["monthly_input"]:
                raise RuntimeError("AI input-token budget exceeded")
            if day[1]+output_tokens>self.limits["daily_output"] or month[1]+output_tokens>self.limits["monthly_output"]:
                raise RuntimeError("AI output-token budget exceeded")
            for counter in (day,month):
                counter[0]+=input_tokens; counter[1]+=output_tokens


class FakeAIProvider:
    def __init__(self, response: dict[str, Any] | None = None):
        self.response=response or {"items":[]}
        self.calls=0

    async def complete(self, *, system_policy: str, untrusted_input: dict[str, Any],
                       schema: dict[str, Any], task_class: str) -> AIResult:
        self.calls+=1
        return AIResult(self.response,0,0,"fake")


def _read_api_key(path_value: str) -> str:
    path=Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("AI credential file unavailable")
    mode=stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError("AI credential file permissions invalid")
    value=path.read_text(encoding="utf-8").strip()
    if not value or "\n" in value:
        raise RuntimeError("AI credential file format invalid")
    return value


class OpenAIResponsesProvider:
    """Optional OpenAI Responses API transport. It has no database or application tools."""
    def __init__(self, *, api_key_file: str, model: str, budget: UsageBudget,
                 transport: httpx.AsyncBaseTransport | None = None):
        if not model or len(model)>100:
            raise ValueError("an explicitly configured model is required")
        self._api_key_file=api_key_file
        self.model=model
        self.budget=budget
        self.transport=transport
        self._semaphore=asyncio.Semaphore(1)

    async def complete(self, *, system_policy: str, untrusted_input: dict[str, Any],
                       schema: dict[str, Any], task_class: str) -> AIResult:
        if len(json.dumps(untrusted_input,ensure_ascii=False))>100_000:
            raise ValueError("AI input exceeds configured limit")
        input_json=json.dumps(untrusted_input,ensure_ascii=False,separators=(",",":"))
        # The model sees minimized job/profile evidence as data; its output cannot trigger tools.
        estimate_in=max(1,(len(system_policy)+len(input_json))//4)
        self.budget.reserve(estimate_in,2048)
        key=_read_api_key(self._api_key_file)
        safe_task=re.sub(r"[^a-z0-9_]","_",task_class.casefold())[:40] or "task"
        body={"model":self.model,"store":False,"max_output_tokens":2048,
              "input":[{"role":"system","content":system_policy},
                       {"role":"user","content":"UNTRUSTED_DATA_JSON:\n"+input_json}],
              "text":{"format":{"type":"json_schema","name":"bruteapply_"+safe_task,
                "strict":True,"schema":schema}}}
        async with self._semaphore:
          async with httpx.AsyncClient(timeout=httpx.Timeout(25,connect=5),follow_redirects=False,
                                       limits=httpx.Limits(max_connections=1),transport=self.transport) as client:
            try:
                response=await client.post("https://api.openai.com/v1/responses",
                    headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"},json=body)
                response.raise_for_status()
                payload=response.json()
            except httpx.HTTPStatusError as exc:
                # Never surface response body: provider errors may echo request material.
                raise RuntimeError(f"AI provider returned HTTP {exc.response.status_code}") from None
            except (httpx.TimeoutException,httpx.RequestError):
                raise RuntimeError("AI provider request failed") from None
        if payload.get("status")!="completed":
            raise RuntimeError("AI provider did not complete the structured response")
        usage=payload.get("usage") or {}; input_tokens=int(usage.get("input_tokens",0)); output_tokens=int(usage.get("output_tokens",0))
        output=[]
        for item in payload.get("output",[]):
            for content in item.get("content",[]):
                if content.get("type")=="refusal":
                    raise RuntimeError("AI provider refused the request")
                if content.get("type")=="output_text": output.append(content.get("text",""))
        try: data=json.loads("".join(output))
        except (json.JSONDecodeError,TypeError): raise RuntimeError("AI provider output was not valid structured JSON") from None
        if not isinstance(data,dict): raise RuntimeError("AI provider output schema validation failed")
        return AIResult(data,input_tokens,output_tokens,self.model)
