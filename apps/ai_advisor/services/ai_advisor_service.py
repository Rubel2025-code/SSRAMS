"""
apps/ai_advisor/services/ai_advisor_service.py

Prompt 1 established this interface (all three method signatures below
are UNCHANGED from that stub). Prompt 7 fully implements it.

Orchestrates: FactsBundleService (Stage 1, deterministic) ->
GeminiService (Stage 2, analysis) -> AIInteraction (audit log). Views
call ONLY this module -- never FactsBundleService or GeminiService
directly (Prompt 7 SS10: "Do not put this entire process directly
inside views"), so the two-stage pattern stays enforced in one place.

Expected flow per method (Prompt 7 SS10), identical shape for all three:
    identify AI task (the method itself)
    -> build task-specific Facts Bundle (FactsBundleService)
    -> create grounded AI prompt (the *_TASK_PROMPT templates below;
       GROUNDING_INSTRUCTIONS itself lives in gemini_service.py and is
       sent as Gemini's system_instruction on every call, not repeated
       here)
    -> GeminiService.generate_guidance()
    -> validate response (GeminiService already validates non-empty/
       usable text; this layer additionally decides ANSWERED vs
       INSUFFICIENT_INFO by checking for the exact FR-19 refusal phrase)
    -> log AIInteraction
    -> return the AIInteraction (callers/views read .ai_response_text,
       .outcome, etc. from it)
"""

from __future__ import annotations

import logging

from apps.accounts.models import StudentProfile
from apps.ai_advisor.models import AIInteraction
from apps.common.enums import AIInteractionType, AIResponseOutcome
from apps.recommendations.services.deadline_service import DeadlineService
from apps.scholarships.models import Scholarship

from .facts_bundle_service import FactsBundleService
from .gemini_service import GeminiService, GeminiServiceError, GeminiServiceUnavailable

logger = logging.getLogger(__name__)

_INSUFFICIENT_INFO_PHRASE = "i don't have enough information to answer this"

_STRATEGY_TASK_PROMPT = (
    "Analyze the scholarships in the Facts Bundle for this student. For each one, help the "
    "student understand: their current position (match score and eligibility), the "
    "scholarship's strengths and weaknesses for them, what to prioritize, what to prepare "
    "for the application, and any deadline-related urgency. Then give a short, sequenced, "
    "practical list of next steps across all the scholarships, prioritizing what to do "
    "first. Use only the facts given; do not invent or recalculate any score, eligibility "
    "status, readiness percentage, or deadline."
)

_PROFILE_IMPROVEMENT_TASK_PROMPT = (
    "The Facts Bundle lists, for this student, which profile gaps recur across how many "
    "scholarships (improvement_opportunities), plus the specific gaps in per_scholarship_gaps. "
    "Explain which profile improvements would help the most (using the affected_scholarship_count "
    "already computed -- do not invent or recompute this number), what specifically is missing, "
    "and practical next steps. Do not guarantee that any improvement will result in acceptance "
    "to any scholarship."
)


class AIAdvisorService:
    """
    Orchestrates: FactsBundleService (Stage 1, deterministic) ->
    GeminiService (Stage 2, analysis) -> AIInteraction (audit log).
    """

    @staticmethod
    def ask_advisor(student: StudentProfile, question: str) -> AIInteraction:
        """
        FR-10: answer a free-form student question. Builds context via
        FactsBundleService.build_for_advisor_question, calls
        GeminiService.generate_guidance, and records an AIInteraction
        with outcome=ANSWERED or INSUFFICIENT_INFO per FR-19.
        """
        facts_bundle = FactsBundleService.build_for_advisor_question(student, question)
        task_prompt = (
            f"The student asked: \"{question}\"\n\n"
            "Answer using only the Facts Bundle above. If the bundle does not contain "
            "enough information to answer this specific question, respond with exactly: "
            "\"I don't have enough information to answer this.\""
        )
        return AIAdvisorService._run(
            student=student,
            interaction_type=AIInteractionType.AI_ADVISOR,
            facts_bundle=facts_bundle,
            task_prompt=task_prompt,
            user_question=question,
            fallback_text=None,  # AI Advisor has no deterministic fallback -- FR-10 is inherently conversational
        )

    @staticmethod
    def generate_strategy(student: StudentProfile, scholarships: list[Scholarship] | None = None) -> AIInteraction:
        """
        FR-22: two-stage Strategy Planner. On GeminiServiceUnavailable,
        must fall back to a deterministic rule-only ranking (urgency +
        match + readiness) and record outcome=FALLBACK_NO_AI rather than
        raising -- "the student is never blocked from getting a plan".

        ``scholarships`` defaults to the student's own current Top
        Opportunities (DeadlineService.top_opportunities) when not
        supplied -- the same already-ranked list Prompt 5 produces, not
        a second ranking computed here.
        """
        if scholarships is None:
            scholarships = [entry.scholarship for entry in DeadlineService.top_opportunities(student, limit=5)]

        facts_bundle = FactsBundleService.build_for_strategy_planner(student, scholarships)
        fallback_text = AIAdvisorService._deterministic_strategy_fallback(facts_bundle)

        return AIAdvisorService._run(
            student=student,
            interaction_type=AIInteractionType.STRATEGY_PLANNER,
            facts_bundle=facts_bundle,
            task_prompt=_STRATEGY_TASK_PROMPT,
            user_question="",
            fallback_text=fallback_text,
        )

    @staticmethod
    def generate_profile_improvement(student: StudentProfile) -> AIInteraction:
        """
        FR-23: two-stage Profile Improvement Advisor. Impact counts
        (e.g. "affects 4 scholarships") must come from
        FactsBundleService, never from Gemini -- Gemini only prioritizes
        and phrases them.
        """
        facts_bundle = FactsBundleService.build_for_profile_improvement(student)
        fallback_text = AIAdvisorService._deterministic_profile_improvement_fallback(facts_bundle)

        return AIAdvisorService._run(
            student=student,
            interaction_type=AIInteractionType.PROFILE_IMPROVEMENT,
            facts_bundle=facts_bundle,
            task_prompt=_PROFILE_IMPROVEMENT_TASK_PROMPT,
            user_question="",
            fallback_text=fallback_text,
        )

    # -------------------------------------------------------------
    # Shared orchestration + deterministic fallbacks
    # -------------------------------------------------------------

    @staticmethod
    def _run(*, student, interaction_type, facts_bundle, task_prompt, user_question, fallback_text) -> AIInteraction:
        """
        Shared Stage-1/Stage-2/log sequence for all three advisors.
        ``fallback_text``, when not None, is the deterministic
        rule-computed plain-structured-list to store (and mark
        FALLBACK_NO_AI) if Gemini is unavailable -- FR-22's explicit
        requirement that the student is never blocked from getting a
        plan. The AI Advisor (ask_advisor) has no such fallback: a
        free-form question has no deterministic equivalent, so it is
        called with fallback_text=None and simply reports the outage.
        """
        service = GeminiService()

        try:
            response_text = service.generate_guidance(facts_bundle, task_prompt)
            outcome = (
                AIResponseOutcome.INSUFFICIENT_INFO
                if _INSUFFICIENT_INFO_PHRASE in response_text.lower()
                else AIResponseOutcome.ANSWERED
            )
            model_name = service.model_name
            latency_ms = getattr(service, "last_latency_ms", None)

        except GeminiServiceUnavailable as exc:
            logger.info("Gemini unavailable for %s: %s", interaction_type, exc)
            if fallback_text is not None:
                response_text = fallback_text
                outcome = AIResponseOutcome.FALLBACK_NO_AI
            else:
                response_text = (
                    "The AI Advisor is currently unavailable because the Gemini API is not "
                    "configured. Your deterministic recommendations, eligibility, and readiness "
                    "results are still fully available elsewhere in SSRAMS."
                )
                outcome = AIResponseOutcome.FALLBACK_NO_AI
            model_name = ""
            latency_ms = None

        except GeminiServiceError as exc:
            logger.warning("Gemini request failed for %s: %s", interaction_type, exc)
            if fallback_text is not None:
                response_text = fallback_text
                outcome = AIResponseOutcome.FALLBACK_NO_AI
            else:
                response_text = (
                    "The AI Advisor could not process your request right now due to a "
                    "temporary error. Please try again shortly."
                )
                outcome = AIResponseOutcome.ERROR
            model_name = ""
            latency_ms = None

        interaction = AIInteraction.objects.create(
            student=student,
            interaction_type=interaction_type,
            user_question=user_question,
            facts_bundle_snapshot=facts_bundle,
            ai_response_text=response_text,
            outcome=outcome,
            gemini_model_name=model_name,
            latency_ms=latency_ms,
        )
        return interaction

    @staticmethod
    def _deterministic_strategy_fallback(facts_bundle: dict) -> str:
        """
        FR-22's required fallback: "a deterministic rule-only ranking
        (combining urgency, match, and readiness)". The scholarships in
        facts_bundle are ALREADY that ranked order (built from
        DeadlineService.top_opportunities, Prompt 5) -- this function
        only formats them as plain text; it does not re-rank or
        recompute anything.
        """
        lines = ["Your scholarships, ranked by match, eligibility, and deadline urgency:"]
        for i, entry in enumerate(facts_bundle.get("scholarships", []), start=1):
            title = entry["scholarship"]["title"]
            score = entry["match_score_percent"]
            status = entry["eligibility_status"]
            readiness = entry["readiness"]["readiness_percent"]
            days = entry["deadline"]["days_remaining"]
            action = entry.get("recommended_action_from_system") or "Review this scholarship's requirements."
            lines.append(
                f"{i}. {title} — {score}% match, {status}, {readiness}% ready, "
                f"{days} day(s) remaining. Recommended: {action}"
            )
        if len(lines) == 1:
            lines.append("No scholarships are currently available to plan around.")
        return "\n".join(lines)

    @staticmethod
    def _deterministic_profile_improvement_fallback(facts_bundle: dict) -> str:
        """FR-22-style fallback for the Profile Improvement Advisor,
        formatting the already-computed improvement_opportunities tally
        as plain text -- no new counting or ranking happens here."""
        lines = ["Profile improvements that could affect the most scholarships:"]
        for opp in facts_bundle.get("improvement_opportunities", []):
            lines.append(
                f"- {opp['criterion']}: affects {opp['affected_scholarship_count']} "
                f"scholarship(s). Example gap: {opp['example_gap'] or 'see details'}"
            )
        if len(lines) == 1:
            lines.append("No specific profile gaps were found across your currently available scholarships.")
        return "\n".join(lines)
