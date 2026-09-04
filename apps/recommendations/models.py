"""
apps/recommendations/models.py

Owns: RecommendationResult, EligibilityResult, ReadinessResult.

These are RESULT/CACHE models — they store the output of deterministic
calculations (FR-06 Match Score + breakdown, FR-07 eligibility + gaps,
FR-08 readiness + action items) so that:

  1. Top Opportunities (FR-20), the AI Strategy Planner (FR-22), and the
     Profile Improvement Advisor (FR-23) can all read the *same* already-
     computed facts instead of recomputing them, which is exactly the
     "Facts Bundle" architecture required by SRS §2.1.1 and the project
     brief's Step 7 dependency chain (Student Profile -> Scholarship
     Criteria + Weights -> Recommendation -> Eligibility + Gaps ->
     Readiness + Deadline -> Top Opportunities -> Facts Bundle -> Gemini).
  2. PR-02 ("recommendation/eligibility/readiness generated within ~2s
     for up to 500 scholarships") is achievable by caching results
     instead of recomputing per request.

The actual scoring/eligibility/readiness ALGORITHMS are not implemented
here (project brief: "DO NOT implement complete eligibility/readiness
calculations" in this prompt) — only the schema that will hold their
output, so apps.recommendations.services (added in a later prompt) has
somewhere to write to and apps.ai_advisor has somewhere to read from.
"""

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.accounts.models import StudentProfile
from apps.common.enums import EligibilityStatus
from apps.common.models import TimeStampedModel
from apps.scholarships.models import Scholarship, ScholarshipCriterion


class RecommendationResult(TimeStampedModel):
    """
    Cached Match Score + per-criterion breakdown for one (student,
    scholarship) pair (FR-06).

    One row per (student, scholarship); recomputed and overwritten
    whenever the profile, criteria, or weights change (the invalidation
    trigger is implemented alongside RecommendationService in a later
    prompt). ``breakdown`` holds the per-criterion contribution shown to
    the student (e.g. {"cgpa": 100, "department": 100, "income": 80,
    "skills": 60}) — SRS §4.1's worked example — computed only from that
    scholarship's own weights (never a global formula, per FR-06).
    """

    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="recommendation_results"
    )
    scholarship = models.ForeignKey(
        Scholarship, on_delete=models.CASCADE, related_name="recommendation_results"
    )

    match_score_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Weighted Match Score using this scholarship's own criteria "
        "weights only (FR-06) — never comparable across scholarships on "
        "formula alone (SRS §4.1).",
    )
    breakdown = models.JSONField(
        default=dict,
        help_text="Per-criterion contribution, e.g. "
        '{"cgpa": 100, "department": 100, "income": 80, "skills": 60}.',
    )
    weak_criteria = models.JSONField(
        default=list,
        help_text="Criteria explicitly flagged as weak/unmatched (FR-06: "
        "'Weak or unmatched criteria shall be flagged explicitly').",
    )

    computed_at = models.DateTimeField(
        auto_now=True,
        help_text="When this cached result was last (re)computed.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "scholarship"], name="unique_recommendation_per_student_scholarship"
            )
        ]
        indexes = [
            models.Index(fields=["student", "match_score_percent"]),
            models.Index(fields=["scholarship"]),
        ]
        # "-id" is a tie-break only and does NOT change the ranking
        # rule (Match Score descending remains the sole ranking signal
        # — FR-06/FR-20). Equal Match Scores are common (e.g. several
        # 100.00% matches), so without a pk tie-break a paginated list
        # of recommendations is not stable across page loads.
        ordering = ["-match_score_percent", "-id"]

    def __str__(self):
        return f"{self.student} x {self.scholarship}: {self.match_score_percent}%"


class EligibilityResult(TimeStampedModel):
    """
    Cached per-criterion eligibility classification + gap explanation
    (FR-07). One row per (student, scholarship, criterion) so each
    criterion's own reason/gap is individually stored, matching FR-07's
    "every failed criterion must carry a reason and, where possible, a
    quantified gap" — a single scholarship-level status could not hold
    per-criterion reasons.
    """

    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="eligibility_results"
    )
    scholarship = models.ForeignKey(
        Scholarship, on_delete=models.CASCADE, related_name="eligibility_results"
    )
    criterion = models.ForeignKey(
        ScholarshipCriterion, on_delete=models.CASCADE, related_name="eligibility_results"
    )

    status = models.CharField(max_length=20, choices=EligibilityStatus.choices)
    explanation = models.CharField(
        max_length=500,
        blank=True,
        help_text='Plain-language reason, e.g. "CGPA is 3.2; required is 3.5" '
        "(FR-07). Blank when status is ELIGIBLE.",
    )
    quantified_gap = models.CharField(
        max_length=100,
        blank=True,
        help_text='Exact shortfall where numeric, e.g. "+0.3 CGPA" (FR-07). '
        "Feeds FR-08 (readiness) and FR-23 (Profile Improvement Advisor).",
    )

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "scholarship", "criterion"],
                name="unique_eligibility_per_student_scholarship_criterion",
            )
        ]
        indexes = [
            models.Index(fields=["student", "scholarship"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.student} x {self.criterion}: {self.get_status_display()}"

    @property
    def is_blocking(self) -> bool:
        """Whether this single criterion result blocks overall eligibility —
        used by the (future) ReadinessService / Top Opportunities logic to
        distinguish a hard block from a 'near-eligible' gap (FR-20)."""
        return self.status == EligibilityStatus.NOT_ELIGIBLE and self.criterion.is_mandatory


class ReadinessResult(TimeStampedModel):
    """
    Cached application readiness + action plan for one (student,
    scholarship) pair (FR-08). One row per (student, scholarship); the
    ordered action plan (FR-08's "recommended next action... first,
    because...") is stored as JSON since its length/order is data-
    driven, not fixed.
    """

    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="readiness_results"
    )
    scholarship = models.ForeignKey(
        Scholarship, on_delete=models.CASCADE, related_name="readiness_results"
    )

    required_items_count = models.PositiveIntegerField()
    completed_items_count = models.PositiveIntegerField()
    readiness_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    missing_items = models.JSONField(
        default=list, help_text='e.g. ["Recommendation Letter", "Income Certificate"].'
    )
    action_plan = models.JSONField(
        default=list,
        help_text="Ordered list of recommended next actions (FR-08), e.g. "
        '["Request the recommendation letter first (longer lead time)."].',
    )

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "scholarship"], name="unique_readiness_per_student_scholarship"
            )
        ]
        indexes = [
            models.Index(fields=["student", "readiness_percent"]),
        ]

    def __str__(self):
        return f"{self.student} x {self.scholarship}: {self.readiness_percent}% ready"
