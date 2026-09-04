"""
Shared enumerations used across multiple SSRAMS apps.

Centralised here so no two apps define their own, slightly different
copy of the same choice list (project brief: "NO duplicate business
logic across apps"). Django model TextChoices classes double as the
source of truth for both model field choices and business-logic checks
in services added in later prompts.
"""

from django.db import models


class RoleChoices(models.TextChoices):
    """The three SSRAMS roles (SRS §2.1 Product Perspective)."""

    STUDENT = "student", "Student"
    PROVIDER = "provider", "Scholarship Provider"
    ADMIN = "admin", "Administrator"


class ProviderVerificationStatus(models.TextChoices):
    """Provider verification state (FR-03)."""

    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class ApplicationStatus(models.TextChoices):
    """
    Scholarship application status lifecycle (FR-09):
    Draft -> Submitted -> Under Review -> Shortlisted -> Approved / Rejected.
    """

    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    UNDER_REVIEW = "under_review", "Under Review"
    SHORTLISTED = "shortlisted", "Shortlisted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class EligibilityStatus(models.TextChoices):
    """Per-criterion and overall eligibility classification (FR-07)."""

    ELIGIBLE = "eligible", "Eligible"
    NOT_ELIGIBLE = "not_eligible", "Not Eligible"
    MISSING_INFO = "missing_info", "Missing Information"


class AIInteractionType(models.TextChoices):
    """
    The two Gemini usage modes (SRS §2.1.1) plus the Profile Improvement
    Advisor, which follows the same Stage 1/Stage 2 pattern as the
    Strategy Planner (FR-23) but is tracked as its own interaction type
    for audit/analytics clarity.
    """

    AI_ADVISOR = "ai_advisor", "Context-Aware AI Advisor (conversational)"
    STRATEGY_PLANNER = "strategy_planner", "AI Strategy Planner (analytical)"
    PROFILE_IMPROVEMENT = "profile_improvement", "Profile Improvement Advisor (analytical)"


class AIResponseOutcome(models.TextChoices):
    """
    Whether Gemini answered from the supplied Facts Bundle or invoked the
    FR-19 "insufficient information" rule. Stored per-interaction so the
    honesty rule is auditable/testable, not just aspirational (FR-19).
    """

    ANSWERED = "answered", "Answered from Facts Bundle"
    INSUFFICIENT_INFO = "insufficient_info", "Declined — Insufficient Information"
    FALLBACK_NO_AI = "fallback_no_ai", "Gemini Unavailable — Deterministic Fallback Used"
    ERROR = "error", "Gemini Request Failed (Error)"
