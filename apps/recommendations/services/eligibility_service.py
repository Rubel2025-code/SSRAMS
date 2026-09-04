"""
apps/recommendations/services/eligibility_service.py

Prompt 1 established this interface (signatures below are UNCHANGED
from that stub). Prompt 4 fully implements it.

Uses the SAME CriterionEvaluationService.evaluate_all() output that
RecommendationService.compute_match_score() uses (SS11) -- this module
never independently re-derives whether a criterion is satisfied.

--------------------------------------------------------------------
ELIGIBILITY LOGIC (SS7) -- NOT derived from Match Score
--------------------------------------------------------------------
Per-criterion status is exactly the CriterionEvaluation.status already
computed (ELIGIBLE / NOT_ELIGIBLE / MISSING_INFO -- the existing
apps.common.enums.EligibilityStatus values, no new enum).

Overall eligibility (EligibilityService.overall_status) is NOT "Match
Score >= some threshold" -- SS7 explicitly forbids that assumption
unless the SRS states it, and the SRS never does. Instead, overall
status is a deterministic rollup of the criterion-level statuses,
mirroring FR-07's plain-English framing ("Not Eligible: <reason>") and
reusing EligibilityResult.is_blocking (Prompt 1's own property,
`status == NOT_ELIGIBLE and criterion.is_mandatory`) as the ONLY thing
that can make a student NOT_ELIGIBLE overall:

  - If ANY mandatory criterion is NOT_ELIGIBLE (is_blocking=True on its
    EligibilityResult) -> overall NOT_ELIGIBLE.
  - Else, if ANY criterion (mandatory or not) is MISSING_INFO -> overall
    MISSING_INFO (SS6: missing information must not be silently upgraded
    to Eligible OR downgraded to Not Eligible -- it is its own outcome).
  - Else (every criterion is ELIGIBLE, or the only non-ELIGIBLE ones are
    NOT_ELIGIBLE-but-optional) -> overall ELIGIBLE.

A scholarship with zero criteria has no basis for a decision and is
treated as overall MISSING_INFO (SS18 "Scholarship with no criteria"),
not silently ELIGIBLE.
"""

from __future__ import annotations

from apps.accounts.models import StudentProfile
from apps.audit.models import AuditLog
from apps.audit.services import log_event
from apps.common.enums import EligibilityStatus
from apps.recommendations.models import EligibilityResult
from apps.scholarships.models import Scholarship

from .criterion_evaluation_service import CriterionEvaluationService


class EligibilityService:
    """Deterministic eligibility classification + gap computation (FR-07)."""

    @staticmethod
    def evaluate(student: StudentProfile, scholarship: Scholarship) -> list[EligibilityResult]:
        """
        Classify each of the scholarship's criteria as Eligible / Not
        Eligible / Missing Information for this student, with a plain-
        language explanation and (where numeric) a quantified gap
        (FR-07). Feeds ReadinessService (FR-08, NOT implemented this
        prompt) and the Profile Improvement Advisor (FR-23, NOT
        implemented this prompt).

        Does NOT validate that the scholarship is published/active/
        non-expired/weight-valid -- unlike RecommendationService,
        eligibility against a student's own profile is meaningful to
        show even for a scholarship a student is merely inspecting
        (e.g. a provider previewing their own unpublished draft, or a
        student viewing a scholarship's criteria before it's confirmed
        recommendable). Callers that specifically need the FR-06
        "is this scholarship recommendable at all" gate should check
        Scholarship.is_visible_to_students / weights_are_valid()
        themselves -- see views.py, which does exactly that before
        calling this for the student-facing recommendation list.
        """
        criteria = list(scholarship.criteria.select_related("weight").all())
        evaluations = CriterionEvaluationService.evaluate_all(student, criteria)
        return EligibilityService._persist_from_evaluations(student, scholarship, evaluations)

    @staticmethod
    def _persist_from_evaluations(student, scholarship, evaluations) -> list[EligibilityResult]:
        """
        Write EligibilityResult rows from an already-computed list of
        CriterionEvaluation objects. Factored out so GapService (which
        also needs the same evaluations for its own GapEntry objects)
        can persist through this exact path instead of calling
        evaluate() a second time and re-querying/re-evaluating the same
        criteria (SS20). evaluate() itself is the normal public entry
        point and remains a thin wrapper around this.
        """
        results = []
        for evaluation in evaluations:
            result, _created = EligibilityResult.objects.update_or_create(
                student=student, scholarship=scholarship, criterion=evaluation.criterion,
                defaults={
                    "status": evaluation.status,
                    "explanation": evaluation.explanation,
                    "quantified_gap": evaluation.quantified_gap,
                },
            )
            results.append(result)

        log_event(
            actor=student.user,
            event_type=AuditLog.EventType.OTHER,
            related_object=scholarship,
            description=f"Eligibility evaluated for '{scholarship.title}' "
            f"({len(results)} criteria).",
        )

        return results

    @staticmethod
    def overall_status(student: StudentProfile, scholarship: Scholarship) -> str:
        """
        Roll up per-criterion EligibilityResult rows into one overall
        status for the scholarship, honoring is_mandatory per criterion.
        See module docstring for the exact rollup rule. Calls evaluate()
        first so the rollup always reflects the current, freshly
        computed per-criterion results rather than a possibly-stale
        cached set (same "recompute on every call" policy as
        RecommendationService.get_or_compute — see that module's docstring).

        Callers that already hold a freshly computed list of
        EligibilityResult (e.g. a view that just called evaluate() for
        its own display purposes) should use rollup_status() directly
        instead, to avoid a redundant third evaluation pass over the
        same criteria (SS20) — see recommendations/views.py.
        """
        results = EligibilityService.evaluate(student, scholarship)
        return EligibilityService.rollup_status(results)

    @staticmethod
    def rollup_status(results: list[EligibilityResult]) -> str:
        """
        Pure rollup rule, factored out of overall_status() so it can be
        applied to an already-computed list of EligibilityResult
        without re-querying/re-evaluating (SS20). See module docstring
        "ELIGIBILITY LOGIC" section for the exact rule.
        """
        if not results:
            return EligibilityStatus.MISSING_INFO

        if any(r.is_blocking for r in results):
            return EligibilityStatus.NOT_ELIGIBLE

        if any(r.status == EligibilityStatus.MISSING_INFO for r in results):
            return EligibilityStatus.MISSING_INFO

        return EligibilityStatus.ELIGIBLE
