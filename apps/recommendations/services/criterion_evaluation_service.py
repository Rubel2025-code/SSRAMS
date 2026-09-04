"""
apps/recommendations/services/criterion_evaluation_service.py

THE SHARED AUTHORITATIVE CRITERION EVALUATION (Prompt 4).

Project brief SS11 requires that RecommendationService (Match Score) and
EligibilityService (eligibility + gaps) never independently interpret
the same criterion two different ways. This module is the single place
that compares one ScholarshipCriterion against one StudentProfile and
produces one CriterionEvaluation -- both RecommendationService and
EligibilityService read from THIS, never re-derive it themselves:

    CriterionEvaluationService.evaluate_all(student, scholarship)
                    |
              evaluations (one CriterionEvaluation per criterion)
               /                                   \\
              v                                     v
    RecommendationService                  EligibilityService
    (sums score_percent * weight)          (persists status/explanation/gap)

This module is NOT a Django app and holds no models of its own -- it is
a pure-Python evaluation function operating on ScholarshipCriterion +
StudentProfile data already loaded by its caller (no independent
queries here; see the "no N+1" note in recommendation_service.py).

--------------------------------------------------------------------
CRITERION SEMANTICS -- reused, not reinvented (SS3, SS6, SS7)
--------------------------------------------------------------------
Uses apps.common.enums.EligibilityStatus exactly as already defined:
ELIGIBLE / NOT_ELIGIBLE / MISSING_INFO. No new status system is
introduced. "Missing Information" here specifically means the piece of
data ScholarshipCriterion.required_value needs to be compared against
is absent/blank on the student's profile -- e.g. `location` is
blank=True on StudentProfile, so a LOCATION criterion with no student
location on file is MISSING_INFO, not NOT_ELIGIBLE. See
_extract_student_value() below for exactly which fields can be missing
and how.

Note that StudentProfile's core numeric fields (cgpa,
family_monthly_income) are NOT NULL at the database level -- a student
cannot exist with a null CGPA, only a *missing StudentProfile entirely*
(registration incomplete) or an empty string/list for the blank-capable
text/JSON fields (location, skills, interests, achievements,
extracurriculars). Both cases are handled: see
evaluate_all()'s early return for "no profile at all" and
_extract_student_value()'s per-field blank/empty checks.

--------------------------------------------------------------------
SCORE CONTRIBUTION POLICY (feeds RecommendationService, SS4/SS5)
--------------------------------------------------------------------
Each CriterionEvaluation carries a `score_percent` in [0, 100] --- this
criterion's own satisfaction level, BEFORE its weight is applied:
  - ELIGIBLE numeric criteria (GTE/LTE) score 100 -- the comparison
    passed; this module does not award partial credit for "exceeding"
    a threshold (e.g. CGPA 3.9 vs required 3.5 is still 100, not
    scaled up), since the SRS's worked example (SS4.1) shows discrete
    100/80/60-style per-criterion contributions tied to satisfaction,
    not a continuous function of margin.
  - ELIGIBLE EQUALS/CONTAINS criteria score 100.
  - NOT_ELIGIBLE criteria score 0 -- an unmet requirement contributes
    nothing to the Match Score, mandatory or not (is_mandatory only
    affects overall *eligibility* rollup, handled in
    EligibilityService.overall_status, not the score).
  - MISSING_INFO criteria score 0 -- SS6 is explicit that missing
    information must not be silently treated as failing eligibility,
    but it still cannot be credited in a weighted score (there is
    nothing to score). The distinction that matters is preserved in
    `status`, not in `score_percent` -- RecommendationResult.weak_criteria
    is where MISSING_INFO criteria are surfaced distinctly from
    NOT_ELIGIBLE ones (see recommendation_service.py).

This policy is documented here once and reused by
RecommendationService's weighted sum -- it is not re-decided per caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from apps.accounts.models import StudentProfile
from apps.common.enums import EligibilityStatus
from apps.scholarships.models import ScholarshipCriterion

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


@dataclass
class CriterionEvaluation:
    """
    One criterion's authoritative evaluation result for one student.
    Plain dataclass (not a model) -- RecommendationService and
    EligibilityService each translate this into their own persisted
    model shape (RecommendationResult.breakdown / EligibilityResult
    rows respectively); this object itself is never persisted.
    """

    criterion: ScholarshipCriterion
    status: str  # an apps.common.enums.EligibilityStatus value
    score_percent: Decimal  # this criterion's own 0/100 satisfaction, pre-weight
    student_value: str  # human-readable, e.g. "3.20", "CSE", "" if missing
    required_value: str  # human-readable, e.g. "3.50", "CSE"
    explanation: str  # plain-language reason, blank when ELIGIBLE
    quantified_gap: str = ""  # e.g. "+0.30 CGPA", blank if not numeric/no gap
    numeric_gap: Decimal | None = field(default=None, repr=False)  # raw Decimal gap, for callers needing the number


def _student_has_profile_data(student: StudentProfile | None) -> bool:
    """Whether there is a StudentProfile row at all (SS18: 'Student
    profile incomplete' -- handled as an absent-profile case, not a
    per-field crash)."""
    return student is not None


def _extract_student_value(student: StudentProfile, criterion_type: str):
    """
    Return (value, is_missing) for one criterion_type read off
    `student`. is_missing=True means "this student has not supplied
    this piece of information" -- the trigger for MISSING_INFO, never
    for NOT_ELIGIBLE.
    """
    CT = ScholarshipCriterion.CriterionType

    if criterion_type == CT.CGPA:
        return student.cgpa, False  # NOT NULL on the model -- never missing once a profile exists
    if criterion_type == CT.INCOME:
        return student.family_monthly_income, False  # NOT NULL -- never missing once a profile exists
    if criterion_type == CT.DEPARTMENT:
        return student.department, False  # NOT NULL -- never missing once a profile exists
    if criterion_type == CT.ACADEMIC_LEVEL:
        return student.academic_level, False  # NOT NULL -- never missing once a profile exists
    if criterion_type == CT.LOCATION:
        value = student.location or ""
        return value, value == ""  # blank=True -- can genuinely be missing
    if criterion_type == CT.SKILLS:
        skills = student.skills or []
        return skills, len(skills) == 0
    if criterion_type == CT.DOCUMENTS:
        # StudentProfile has no "documents submitted" concept -- that is
        # Application-scoped (apps.applications), out of reach and out
        # of scope for this prompt (SS23: no application logic). A
        # DOCUMENTS-type ScholarshipCriterion is therefore always
        # MISSING_INFO from the recommendation engine's point of view;
        # actual document fulfillment is evaluated by ReadinessService
        # in a later prompt.
        return None, True
    if criterion_type == CT.OTHER:
        # No structured student field corresponds to a provider-defined
        # "other" criterion -- always MISSING_INFO, deterministically,
        # rather than guessing at a match.
        return None, True

    return None, True


def _compare_numeric(student_value: Decimal, required_value_raw: str, comparison: str):
    """
    Returns (is_satisfied, numeric_gap) for a GTE/LTE numeric
    comparison. numeric_gap is the Decimal amount by which the student
    falls short (always >= 0; None if satisfied). Raises ValueError if
    required_value_raw is not a valid Decimal -- callers translate that
    into a MISSING_INFO/invalid-configuration result rather than crashing.
    """
    try:
        required = Decimal(str(required_value_raw))
    except InvalidOperation:
        raise ValueError(f"Criterion required_value '{required_value_raw}' is not numeric.")

    Comparison = ScholarshipCriterion.Comparison
    if comparison == Comparison.GTE:
        if student_value >= required:
            return True, None
        return False, required - student_value
    if comparison == Comparison.LTE:
        if student_value <= required:
            return True, None
        return False, student_value - required

    raise ValueError(f"Comparison '{comparison}' is not valid for a numeric criterion.")


def _compare_text(student_value, required_value_raw: str, comparison: str):
    """
    Returns is_satisfied for EQUALS/CONTAINS comparisons against
    non-numeric student data. `student_value` may be a string
    (department, academic_level, location) or a list (skills).
    CONTAINS on a list checks that every comma-separated required token
    is present (case-insensitive); CONTAINS on a string does a
    case-insensitive substring check. EQUALS does a case-insensitive
    exact match on strings.
    """
    Comparison = ScholarshipCriterion.Comparison
    required_tokens = [t.strip().lower() for t in required_value_raw.split(",") if t.strip()]

    if isinstance(student_value, list):
        student_tokens = {str(v).strip().lower() for v in student_value}
        if comparison == Comparison.CONTAINS:
            return all(token in student_tokens for token in required_tokens)
        if comparison == Comparison.EQUALS:
            return student_tokens == set(required_tokens)
        raise ValueError(f"Comparison '{comparison}' is not valid for a list-valued criterion.")

    student_text = str(student_value).strip().lower()
    if comparison == Comparison.EQUALS:
        return student_text == required_value_raw.strip().lower()
    if comparison == Comparison.CONTAINS:
        return any(token in student_text for token in required_tokens)

    raise ValueError(f"Comparison '{comparison}' is not valid for a text criterion.")


_NUMERIC_TYPES = {ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.CriterionType.INCOME}


class CriterionEvaluationService:
    """The shared, authoritative per-criterion evaluator (see module docstring)."""

    @staticmethod
    def evaluate_one(student: StudentProfile | None, criterion: ScholarshipCriterion) -> CriterionEvaluation:
        """
        Evaluate exactly one criterion against exactly one student.
        Never raises for ordinary data conditions (missing profile,
        missing field, malformed required_value) -- those all become a
        MISSING_INFO CriterionEvaluation with an explanatory message
        (SS18: 'Do not crash. Return a meaningful deterministic result').
        """
        required_display = criterion.required_value

        if not _student_has_profile_data(student):
            return CriterionEvaluation(
                criterion=criterion, status=EligibilityStatus.MISSING_INFO,
                score_percent=ZERO, student_value="", required_value=required_display,
                explanation="Student profile is incomplete; this criterion cannot be evaluated.",
            )

        student_value, is_missing = _extract_student_value(student, criterion.criterion_type)

        if is_missing:
            return CriterionEvaluation(
                criterion=criterion, status=EligibilityStatus.MISSING_INFO,
                score_percent=ZERO, student_value="", required_value=required_display,
                explanation=f"Missing information: your profile does not include "
                f"{criterion.get_criterion_type_display().lower()}.",
            )

        try:
            if criterion.criterion_type in _NUMERIC_TYPES:
                is_satisfied, numeric_gap = _compare_numeric(
                    Decimal(student_value), criterion.required_value, criterion.comparison
                )
                student_display = str(student_value)
            else:
                is_satisfied = _compare_text(student_value, criterion.required_value, criterion.comparison)
                numeric_gap = None
                student_display = (
                    ", ".join(str(v) for v in student_value)
                    if isinstance(student_value, list) else str(student_value)
                )
        except ValueError as exc:
            # Malformed criterion configuration (e.g. a CGPA criterion
            # whose required_value isn't numeric, or a comparison that
            # doesn't apply to this criterion_type). This should already
            # be prevented at creation time by
            # apps.scholarships.services.criteria_service, but the
            # evaluator fails safely rather than crashing or silently
            # guessing (SS13, SS18).
            return CriterionEvaluation(
                criterion=criterion, status=EligibilityStatus.MISSING_INFO,
                score_percent=ZERO, student_value="", required_value=required_display,
                explanation=f"This criterion has an invalid configuration and cannot be evaluated: {exc}",
            )

        if is_satisfied:
            return CriterionEvaluation(
                criterion=criterion, status=EligibilityStatus.ELIGIBLE,
                score_percent=ONE_HUNDRED, student_value=student_display,
                required_value=required_display, explanation="",
            )

        # Not satisfied.
        quantified_gap = ""
        if numeric_gap is not None:
            unit = "CGPA" if criterion.criterion_type == ScholarshipCriterion.CriterionType.CGPA else ""
            quantified_gap = f"+{numeric_gap} {unit}".strip()
        explanation = (
            f"Not eligible: {criterion.get_criterion_type_display()} is "
            f"{student_display}; required is {criterion.get_comparison_display().lower()} "
            f"{required_display}."
        )
        return CriterionEvaluation(
            criterion=criterion, status=EligibilityStatus.NOT_ELIGIBLE,
            score_percent=ZERO, student_value=student_display,
            required_value=required_display, explanation=explanation,
            quantified_gap=quantified_gap, numeric_gap=numeric_gap,
        )

    @staticmethod
    def evaluate_all(student: StudentProfile | None, criteria) -> list[CriterionEvaluation]:
        """
        Evaluate every criterion in `criteria` (an iterable of
        ScholarshipCriterion, expected to already be
        select_related('weight') by the caller -- see
        recommendation_service.py's "no N+1" note) against `student`.
        Returns one CriterionEvaluation per criterion, in the same
        order. Never raises; a scholarship with zero criteria simply
        returns an empty list, which callers (RecommendationService,
        EligibilityService) interpret according to their own rules
        (see SS18 "Scholarship with no criteria").
        """
        return [CriterionEvaluationService.evaluate_one(student, criterion) for criterion in criteria]
