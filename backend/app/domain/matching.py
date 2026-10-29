from __future__ import annotations

from dataclasses import dataclass
import re


SKILL_ALIASES = {
    "active directory": ("active directory", "ad ds"),
    "linux": ("linux", "ubuntu", "debian", "centos"),
    "docker": ("docker", "containerized", "containers"),
    "networking": ("tcp/ip", "dns", "dhcp", "vpn", "vlan"),
    "cloud": ("azure", "aws", "cloudflare"),
    "monitoring": ("monitoring", "uptime kuma", "netdata", "better stack"),
    "support": ("technical support", "help desk", "troubleshooting", "desktop support"),
    "automation": ("powershell", "bash", "terraform", "automation"),
}
HARD_BLOCKERS = {
    "security_clearance": "Required security clearance is not confirmed.",
    "work_authorization": "Work authorization is not confirmed by an approved profile fact.",
    "mandatory_license": "A mandatory license or credential is not confirmed.",
}


@dataclass(frozen=True)
class MatchResult:
    score: int
    confidence: float
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    blockers: tuple[str, ...]
    recommendation: str
    explanation: str


def score_job(description: str, verified_skills: set[str], *, requirements: list[str] | None = None,
              blockers: list[str] | None = None, seniority_fit: bool = True,
              location_fit: bool = True) -> MatchResult:
    """Deterministic keyword-based evidence; never infers sensitive eligibility."""
    text = re.sub(r"\s+", " ", description.casefold())
    normalized_facts = {x.casefold() for x in verified_skills}
    required = requirements or list(SKILL_ALIASES)
    matched, missing = [], []
    for skill in required:
        aliases = SKILL_ALIASES.get(skill.casefold(), (skill.casefold(),))
        found = any(a in text for a in aliases) and any(
            fact == skill.casefold() or fact in aliases for fact in normalized_facts
        )
        (matched if found else missing).append(skill)
    hard = tuple(blockers or ())
    denominator = max(1, len(required))
    score = round(100 * len(matched) / denominator)
    if not seniority_fit or not location_fit:
        score = min(score, 49)
    if hard:
        score = min(score, 24)
    confidence = min(0.95, 0.45 + 0.08 * len(required)) if description else 0.2
    recommendation = "BLOCKED" if hard else "REVIEW" if score >= 50 else "SKIP"
    reasons = [f"{len(matched)} of {len(required)} configured skill groups have verified evidence"]
    if not seniority_fit:
        reasons.append("seniority is outside the configured target")
    if not location_fit:
        reasons.append("location is outside the configured target")
    if hard:
        reasons.append("hard eligibility blockers need owner-approved facts")
    return MatchResult(score, round(confidence, 2), tuple(matched), tuple(missing), hard,
                       recommendation, "; ".join(reasons))
