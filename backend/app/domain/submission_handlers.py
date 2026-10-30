"""Capability declarations and fail-closed application handler selection."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class HandlerCapability(StrEnum):
    AUTOMATION_PERMITTED = "AUTOMATION_PERMITTED"
    MANUAL_ONLY = "MANUAL_ONLY"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    CAPTCHA_BLOCKED = "CAPTCHA_BLOCKED"
    MFA_BLOCKED = "MFA_BLOCKED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class HandlerDeclaration:
    provider: str
    capability: HandlerCapability
    terms_reviewed: bool
    idempotent: bool
    confirmation_supported: bool


@dataclass(frozen=True)
class SubmissionResult:
    attempted: bool
    reliable_confirmation: bool
    confirmation_id: str | None
    reason: str


class SubmissionHandler(Protocol):
    declaration: HandlerDeclaration
    def submit(self, *, idempotency_key: str, document_refs: tuple[str,...]) -> SubmissionResult: ...


def handler_decision(declaration: HandlerDeclaration | None, *, platform_allows: bool,
                     required_inputs_complete: bool, sensitive_answers_complete: bool,
                     duplicate_check_passed: bool, captcha: bool, mfa: bool, authenticated: bool) -> tuple[bool,str]:
    if not declaration:
        return False,"UNSUPPORTED"
    if captcha or declaration.capability==HandlerCapability.CAPTCHA_BLOCKED:
        return False,"CAPTCHA_BLOCKED"
    if mfa or declaration.capability==HandlerCapability.MFA_BLOCKED:
        return False,"MFA_BLOCKED"
    if declaration.capability==HandlerCapability.AUTH_REQUIRED and not authenticated:
        return False,"AUTH_REQUIRED"
    if declaration.capability not in {HandlerCapability.AUTOMATION_PERMITTED,HandlerCapability.AUTH_REQUIRED} \
       or not platform_allows or not declaration.terms_reviewed:
        return False,"MANUAL_ONLY"
    if not required_inputs_complete or not sensitive_answers_complete:
        return False,"MISSING_OWNER_INPUT"
    if not duplicate_check_passed or not declaration.idempotent:
        return False,"DUPLICATE_OR_IDEMPOTENCY_UNSAFE"
    if not declaration.confirmation_supported:
        return False,"SUBMISSION_UNVERIFIABLE"
    return True,"POLICY_PASSED"


class FakeSubmissionHandler:
    """Test-only adapter; it never contacts an employer or provider."""
    declaration=HandlerDeclaration("fake",HandlerCapability.AUTOMATION_PERMITTED,True,True,True)

    def submit(self, *, idempotency_key: str, document_refs: tuple[str,...]) -> SubmissionResult:
        if not idempotency_key or not document_refs:
            return SubmissionResult(False,False,None,"MISSING_TEST_INPUT")
        return SubmissionResult(True,True,"FAKE-CONFIRMATION-ONLY","FAKE_PROVIDER")
