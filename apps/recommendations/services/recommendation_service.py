"""
apps/recommendations/services/recommendation_service.py

Prompt 1 established this interface (signatures below are UNCHANGED
from that stub -- see the module's original docstring, preserved here).
Prompt 4 fully implements it.

Consumers (already known at foundation time, so the signature is fixed
now to avoid breaking them later):
  - apps.recommendations views/templates (Top Opportunities, FR-20)
  - apps.ai_advisor.services.facts_bundle_service (reads
    RecommendationResult rows this service writes)

--------------------------------------------------------------------
MATCH SCORE FORMULA (SS4) AND PRECISION POLICY (SS5)
--------------------------------------------------------------------
Match Score = sum( criterion.score_percent * criterion.weight_percent / 100 )
              over every criterion belonging to THIS scholarship, using
              ONLY this scholarship's own ScholarshipCriterionWeight
              rows (FR-06; never a global/platform formula).

criterion.score_percent comes from
CriterionEvaluationService.evaluate_one() (0 or 100 -- see that
module's "SCORE CONTRIBUTION POLICY" docstring section for why no
partial credit is given for exceeding a threshold, and why
MISSING_INFO scores 0 without being conflated with NOT_ELIGIBLE).

Precision: every intermediate multiplication/summation is done in
Decimal at FULL precision -- no per-criterion rounding. Only the FINAL
total is rounded, to 2 decimal places using ROUND_HALF_UP, to match
RecommendationResult.match_score_percent's `decimal_places=2` field
definition. This is the one rounding step in the whole pipeline; SS5
explicitly warns against rounding intermediate values in a way that
could change the result, so summation always happens on unrounded
Decimal contributions.

Worked example (matches SRS SS4.1's shape): a scholarship weights CGPA
40%, Department 30%, Income 20%, Skills 10%. If the student is ELIGIBLE
on CGPA/Department/Income but MISSING_INFO on Skills:
    (100 * 40/100) + (100 * 30/100) + (100 * 20/100) + (0 * 10/100)
  = 40.00 + 30.00 + 20.00 + 0.00 = 90.00
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError

from apps.accounts.models import StudentProfile
from apps.recommendations.models import RecommendationResult
from apps.scholarships.models import Scholarship
from apps.scholarships.services.weight_service import WeightService

from .criterion_evaluation_service import CriterionEvaluationService

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


def _validate_scholarship_recommendable(scholarship: Scholarship) -> None:
    """
    SS12 (published scholarships only) + SS13 (invalid configuration
    fails safely). Reuses apps.scholarships' own rules rather than
    duplicating them -- Scholarship.is_visible_to_students already
    encodes published+active+not-expired (Prompt 3), and
    WeightService.validate_configuration already encodes the 100%-sum
    check (also Prompt 3). This function does not re-implement either
    rule, only calls them and raises with a clear, specific reason.
    """
    if not scholarship.is_published:
        raise ValidationError(f"'{scholarship.title}' is not published and cannot be scored.")
    if not scholarship.is_active:
        raise ValidationError(f"'{scholarship.title}' has been deactivated and cannot be scored.")
    if scholarship.is_expired:
        raise ValidationError(f"'{scholarship.title}' has passed its deadline and cannot be scored.")

    validation = WeightService.validate_configuration(scholarship)
    if not validation.is_valid:
        raise ValidationError(
            f"'{scholarship.title}' does not have a valid weight configuration "
            f"and cannot be scored: " + " ".join(validation.issues)
        )


class RecommendationService:
    """Deterministic Match Score computation (FR-06). No AI involved."""

    @staticmethod
    def compute_match_score(student: StudentProfile, scholarship: Scholarship) -> RecommendationResult:
        """
        Compute (or recompute) the weighted Match Score for one student
        against one scholarship, using ONLY that scholarship's own
        criteria weights (FR-06) — never a global formula.

        Raises ValidationError if ``scholarship.weights_are_valid()`` is
        False, or if the scholarship is not currently recommendable
        (unpublished/inactive/expired) — FR-06: "a scholarship without
        completed weights is not yet eligible for recommendation
        scoring."

        Deterministic: given the same student profile, criteria, and
        weights, this always produces the same RecommendationResult
        (SS2) -- no randomness, no ML, no Gemini calls anywhere in this
        method or anything it calls.
        """
        _validate_scholarship_recommendable(scholarship)

        # Single query for all criteria + their weights (avoids N+1 --
        # SS20) -- both this method and EligibilityService.evaluate use
        # the identical select_related('weight') queryset shape so
        # neither re-queries what the other already loaded in the same
        # request (they are still separate calls/queries across the two
        # services, but each individually stays O(1) queries for criteria).
        criteria = list(scholarship.criteria.select_related("weight").all())

        evaluations = CriterionEvaluationService.evaluate_all(student, criteria)

        total = ZERO
        breakdown = {}
        weak_criteria = []

        for evaluation in evaluations:
            weight_percent = evaluation.criterion.weight.weight_percent
            contribution = (evaluation.score_percent * weight_percent) / Decimal("100")
            total += contribution

            key = evaluation.criterion.criterion_type
            breakdown[key] = int(evaluation.score_percent)  # 0 or 100, matches SRS SS4.1's display convention

            if evaluation.score_percent == ZERO:
                weak_criteria.append({
                    "criterion_type": key,
                    "status": evaluation.status,
                    "explanation": evaluation.explanation,
                })

        # The one rounding step in the whole calculation (SS5) — full
        # Decimal precision is kept through every summation above;
        # only the final total is quantized, using ROUND_HALF_UP so the
        # policy is explicit and reproducible rather than relying on
        # Decimal's default (ROUND_HALF_EVEN / banker's rounding) implicitly.
        final_score = total.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        result, _created = RecommendationResult.objects.update_or_create(
            student=student, scholarship=scholarship,
            defaults={
                "match_score_percent": final_score,
                "breakdown": breakdown,
                "weak_criteria": weak_criteria,
            },
        )

        # Deliberately NOT logged to AuditLog: Match Score computation
        # happens on every recommendation-list/detail page view (SS17),
        # so logging every call would flood the audit trail with
        # low-value, high-frequency entries -- SS21 asks for "important"
        # events "where appropriate," not every read-like computation.
        # What IS audit-logged in this prompt is the eligibility
        # determination (EligibilityService.evaluate, below), which is
        # the more meaningful "here is this student's authoritative
        # status" event, logged once per explicit evaluation request
        # rather than implicitly on every score refresh.

        return result

    @staticmethod
    def get_or_compute(student: StudentProfile, scholarship: Scholarship) -> RecommendationResult:
        """
        Return the cached RecommendationResult, computing it first if
        absent. Always recomputes rather than trusting a stale cache
        indefinitely -- Prompt 4 has no invalidation-trigger
        infrastructure (e.g. signals on profile/criteria/weight change)
        yet, so "freshness" for this prompt means "recompute on every
        call that needs a guaranteed-current value." A later prompt may
        add a genuine cache-with-invalidation if PR-02's performance
        target requires it; documented here rather than silently assumed.
        """
        return RecommendationService.compute_match_score(student, scholarship)

    @staticmethod
    def rank_for_student(student: StudentProfile):
        """
        Return all currently recommendable (published, active,
        non-expired, weight-valid) scholarships ranked by Match Score
        for this student — the input to Top Opportunities (FR-20, NOT
        implemented in this prompt; this method only produces the
        ranked RecommendationResult list a later prompt's
        DeadlineService.top_opportunities will consume).

        Scholarships that fail _validate_scholarship_recommendable are
        silently excluded here (not an error) — SS18 "no matching
        scholarships" returns an empty list, not an exception.
        """
        candidates = Scholarship.objects.filter(
            is_published=True, is_active=True,
        ).select_related("provider").prefetch_related("criteria__weight")

        results = []
        for scholarship in candidates:
            if scholarship.is_expired:
                continue
            try:
                _validate_scholarship_recommendable(scholarship)
            except ValidationError:
                continue
            results.append(RecommendationService.compute_match_score(student, scholarship))

        results.sort(key=lambda r: r.match_score_percent, reverse=True)
        return results
