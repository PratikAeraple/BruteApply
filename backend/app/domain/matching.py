from __future__ import annotations

from dataclasses import dataclass, field
import re


SKILL_ALIASES = {
    "active directory": ("active directory", "ad ds"),
    "windows": ("windows", "windows server"),
    "microsoft 365": ("microsoft 365", "office 365", "m365"),
    "endpoint administration": ("intune", "endpoint administration", "endpoint management", "device management"),
    "linux": ("linux", "ubuntu", "debian", "centos"),
    "docker": ("docker", "containerized", "containers"),
    "networking": ("network troubleshooting", "tcp/ip", "wifi", "wi-fi", "dns", "dhcp", "vpn", "vlan"),
    "cloud": ("azure", "aws", "cloudflare"),
    "monitoring": ("monitoring", "uptime kuma", "netdata", "better stack"),
    "support": ("technical support", "help desk", "service desk", "troubleshooting", "desktop support"),
    "hardware": ("hardware support", "hardware troubleshooting", "device deployment", "computer hardware"),
    "virtualization": ("proxmox", "vmware", "hyper-v", "virtualization"),
    "automation": ("powershell", "bash", "terraform", "automation"),
    "backup and recovery": ("backup", "recovery", "disaster recovery", "restore"),
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
    breakdown: dict[str, int] = field(default_factory=dict)


def score_job(description: str, verified_skills: set[str], *, requirements: list[str] | None = None,
              blockers: list[str] | None = None, seniority_fit: bool | None = None,
              location_fit: bool | None = None, work_mode_fit: bool | None = None,
              province_fit: bool | None = None, experience_fit: bool | None = None,
              certification_fit: bool | None = None, education_fit: bool | None = None,
              project_fit: bool | None = None) -> MatchResult:
    """Score deterministic evidence only; unknown candidate facts are not inferred.

    The optional fit inputs must come from explicitly recorded profile facts or
    owner preferences.  This function intentionally does not infer legal,
    medical, demographic, work-authorisation, or security-clearance facts from
    job-description text.
    """
    text = re.sub(r"\s+", " ", description.casefold())
    normalized_facts = {re.sub(r"\s+", " ", x.casefold()).strip() for x in verified_skills}
    required = requirements or list(SKILL_ALIASES)
    matched, missing = [], []
    for skill in required:
        aliases = SKILL_ALIASES.get(skill.casefold(), (skill.casefold(),))
        found = any(a in text for a in aliases) and any(
            fact == skill.casefold() or any(alias in fact for alias in aliases)
            for fact in normalized_facts
        )
        (matched if found else missing).append(skill)
    hard = tuple(blockers or ())
    # Skill evidence remains the largest component. Other dimensions contribute
    # only when the caller has explicit evidence; unknown is omitted rather than
    # treated as a match or mismatch.
    fit_inputs = {
        "seniority": seniority_fit,
        "location": location_fit,
        "province": province_fit,
        "work_mode": work_mode_fit,
        "experience": experience_fit,
        "certifications": certification_fit,
        "education": education_fit,
        "projects": project_fit,
    }
    explicit = [(name, value) for name, value in fit_inputs.items() if value is not None]
    skill_weight = 60 if explicit else 100
    dimensions: dict[str, int] = {
        "skills": round(skill_weight * len(matched) / max(1, len(required))),
    }
    if explicit:
        # Location/seniority/experience have the greatest operational weight;
        # the rest refine ranking without overruling verified hard constraints.
        weights = {"seniority": 10, "location": 10, "province": 6, "work_mode": 5,
                   "experience": 8, "certifications": 4, "education": 4, "projects": 3}
        available_weight = sum(weights[name] for name, _ in explicit)
        fit_points = sum(weights[name] for name, value in explicit if value)
        dimensions["profile_fit"] = round(40 * fit_points / max(1, available_weight))
    score = min(100, sum(dimensions.values()))
    if any(value is False for value in (seniority_fit, location_fit, province_fit, work_mode_fit,
                                        experience_fit, certification_fit, education_fit, project_fit)):
        score = min(score, 49)
    if hard:
        score = min(score, 24)
    evidence_ratio = len(explicit) / len(fit_inputs)
    confidence = min(0.95, 0.35 + 0.08 * min(5, len(required)) + 0.3 * evidence_ratio) if description else 0.2
    recommendation = "BLOCKED" if hard else "REVIEW" if score >= 50 else "SKIP"
    reasons = [f"{len(matched)} of {len(required)} configured skill groups have verified evidence"]
    if seniority_fit is False:
        reasons.append("seniority is outside the configured target")
    if location_fit is False:
        reasons.append("location is outside the configured target")
    if province_fit is False:
        reasons.append("province does not match an owner-configured preference")
    if work_mode_fit is False:
        reasons.append("work mode does not match an owner-configured preference")
    if experience_fit is False:
        reasons.append("required experience is not supported by verified profile evidence")
    if certification_fit is False:
        reasons.append("a required certification is not confirmed by verified evidence")
    if education_fit is False:
        reasons.append("required education is not confirmed by verified evidence")
    if project_fit is False:
        reasons.append("no relevant verified project evidence was found")
    if hard:
        reasons.append("hard eligibility blockers need owner-approved facts")
    return MatchResult(score, round(confidence, 2), tuple(matched), tuple(missing), hard,
                       recommendation, "; ".join(reasons), dimensions)
