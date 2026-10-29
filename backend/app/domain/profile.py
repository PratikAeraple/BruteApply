from __future__ import annotations

import re


FACT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


def validate_profile_facts(facts: dict[str, str]) -> dict[str, str]:
    if len(facts) > 80:
        raise ValueError("too many profile facts")
    cleaned: dict[str, str] = {}
    for key, value in facts.items():
        if not FACT_KEY.fullmatch(key) or not isinstance(value, str):
            raise ValueError("invalid profile fact")
        value = value.strip()
        if len(value) > 5000:
            raise ValueError("profile fact exceeds limit")
        cleaned[key] = value
    return cleaned


def merge_profile_facts(existing: dict[str, str], incoming: dict[str, str]) -> dict[str, str]:
    result = dict(existing)
    for key, value in incoming.items():
        if value:
            result[key] = value
        else:
            result.pop(key, None)
    return result
