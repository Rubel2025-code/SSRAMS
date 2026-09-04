"""
apps/recommendations/services/gap_service.py

Owns: criterion-level eligibility gap analysis (SS8, SS16).

No dedicated gap model exists (Prompt 1 did not create one -- see
apps/recommendations/models.py: only RecommendationResult,
EligibilityResult, ReadinessResult). Rather than create a new Django
app or a new model, this service is a thin, factual layer that reuses
CriterionEvaluationService's output DIRECTLY (the same authoritative
evaluations EligibilityService persists) rather than reading back the
persisted EligibilityResult rows -- EligibilityResult does not store
the student's raw current value (only the composed `explanation`
sentence and `quantified_gap`), so re-deriving current_value from
persisted text would mean parsing a human-readable string back into
data. Going straight to CriterionEvaluation, which already carries
`student_value` as a clean field, avoids that and keeps this service
consistent with SS11's "same authoritative evaluations" requirement in
the more direct way.

EligibilityService.evaluate() is still called here (so the persisted
EligibilityResult rows stay up to date as a side effect of asking for
gaps -- the same "recompute reflects current state" policy used
throughout this prompt), but the GapEntry values themselves come from
CriterionEvaluationService's evaluation objects, not from parsing
EligibilityResult.explanation.

Output shape (SS16): one GapEntry per NOT_ELIGIBLE or MISSING_INFO
criterion, exposing exactly:
    criterion_type / criterion_display -- which criterion
    current_value   -- what the student currently has ("" if missing)
    required_value  -- what the scholarship requires
    gap             -- the quantified shortfall string, "" if not numeric/unknown
    status          -- the EligibilityStatus value (NOT_ELIGIBLE or MISSING_INFO)
    explanation     -- the plain-language reason, matching EligibilityResult.explanation

This is a purely factual report (SS8: "Do not fabricate recommendations
... report factual differences. Strategic advice belongs to later
modules.") -- it computes no suggestions, only states the difference.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.accounts.models import StudentProfile
from apps.common.enums import EligibilityStatus
from apps.scholarships.models import Scholarship

from .criterion_evaluation_service import CriterionEvaluationService
from .eligibility_service import EligibilityService


@dataclass
class GapEntry:
    """One factual gap for one criterion. See module docstring for field meanings."""

    criterion_type: str
    criterion_display: str
    current_value: str
    required_value: str
    gap: str
    status: str
    explanation: str


class GapService:
    """Read-only factual gap reporting, built on CriterionEvaluationService (SS8, SS16)."""

    @staticmethod
    def get_gaps(student: StudentProfile, scholarship: Scholarship) -> list[GapEntry]:
        """
        Return one GapEntry for every criterion that is currently
        NOT_ELIGIBLE or MISSING_INFO for this student/scholarship pair.
        ELIGIBLE criteria have no gap and are omitted (an empty list
        means "no gaps" -- SS22 "No gaps" test case).

        Also persists/refreshes the corresponding EligibilityResult rows
        via EligibilityService's own shared persistence helper (one
        query for criteria, one evaluation pass, reused for both the
        persisted results and the returned gaps) rather than calling
        EligibilityService.evaluate() a second time, which would
        re-query and re-evaluate the same criteria redundantly (SS20:
        avoid repeatedly evaluating the same criteria within one
        recommendation calculation).
        """
        criteria = list(scholarship.criteria.select_related("weight").all())
        evaluations = CriterionEvaluationService.evaluate_all(student, criteria)

        # Persist through EligibilityService's shared helper so there is
        # exactly one code path that writes EligibilityResult rows,
        # matching SS11 ("do not have two places independently
        # calculating/persisting the same thing").
        EligibilityService._persist_from_evaluations(student, scholarship, evaluations)

        gaps = []
        for evaluation in evaluations:
            if evaluation.status == EligibilityStatus.ELIGIBLE:
                continue

            gaps.append(GapEntry(
                criterion_type=evaluation.criterion.criterion_type,
                criterion_display=evaluation.criterion.get_criterion_type_display(),
                current_value=evaluation.student_value,
                required_value=evaluation.required_value,
                gap=evaluation.quantified_gap,
                status=evaluation.status,
                explanation=evaluation.explanation,
            ))

        return gaps
