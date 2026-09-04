"""
apps/ai_advisor/models.py

Owns: AIInteraction.

One row per Gemini call (conversational AI Advisor, Strategy Planner,
or Profile Improvement Advisor). This is what makes FR-19 ("enforced
insufficient-information rule... implemented, not just stated") and
SRS §3.3.3 ("must never present an AI-authored number as fact") into
auditable facts rather than aspirations: every interaction stores
exactly the Facts Bundle that was sent, so a supervisor/tester can
later verify Gemini's response never introduced a number that was not
already in facts_bundle_snapshot.

This model does NOT store Gemini credentials or make the call itself —
that is services/gemini_service.py's job (below). This is purely the
audit/history record.
"""

from django.db import models

from apps.accounts.models import StudentProfile
from apps.common.enums import AIInteractionType, AIResponseOutcome
from apps.common.models import TimeStampedModel


class AIInteraction(TimeStampedModel):
    """Audit record of one Gemini request/response cycle."""

    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="ai_interactions"
    )
    interaction_type = models.CharField(max_length=30, choices=AIInteractionType.choices)

    user_question = models.TextField(
        blank=True,
        help_text="The student's free-form question (AI Advisor only, FR-10). "
        "Blank for Strategy Planner / Profile Improvement Advisor, which are "
        "triggered by a fixed request, not an open question.",
    )

    # The exact structured facts Django sent to Gemini (SRS §2.1.1: "Django
    # first retrieves and computes... and only then sends that context to
    # Gemini"). Snapshotting it here — rather than only logging Gemini's
    # response — is what lets FR-19 compliance be checked after the fact:
    # every number in ai_response_text must trace back to a value already
    # present in this JSON blob.
    facts_bundle_snapshot = models.JSONField(
        default=dict,
        help_text="Exact Facts Bundle sent to Gemini for this interaction "
        "(never raw DB access — see apps.ai_advisor app docstring).",
    )

    ai_response_text = models.TextField(blank=True)
    outcome = models.CharField(
        max_length=30,
        choices=AIResponseOutcome.choices,
        help_text="Whether Gemini answered from the bundle, declined per "
        "FR-19, or the deterministic fallback was used because Gemini was "
        "unavailable (FR-22).",
    )

    gemini_model_name = models.CharField(max_length=100, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        # "-id" is a tie-breaker only (see ProviderVerification.Meta for
        # the same note): two interactions created inside the same clock
        # tick share a created_at, and the views read "the latest
        # interaction" with .first(), so the tie must be deterministic.
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["student", "interaction_type"]),
            models.Index(fields=["outcome"]),
        ]

    def __str__(self):
        return f"{self.get_interaction_type_display()} for {self.student} [{self.outcome}]"
