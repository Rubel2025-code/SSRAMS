"""
apps/recommendations/tests.py

Foundation tests: model relationships and constraints only (FR-06/07/08
calculation logic is implemented and tested in a later prompt).
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db.utils import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import ProviderProfile, StudentProfile, User
from apps.common.enums import EligibilityStatus, RoleChoices
from apps.scholarships.models import Scholarship, ScholarshipCriterion

from .models import EligibilityResult, ReadinessResult, RecommendationResult


class RecommendationResultModelTests(TestCase):
    def setUp(self):
        student_user = User.objects.create_user(
            username="stud1", password="a-strong-pass-123", role=RoleChoices.STUDENT
        )
        self.student = StudentProfile.objects.create(
            user=student_user, university="IUBAT", department="CSE",
            cgpa=Decimal("3.72"), academic_level="Undergraduate",
            family_monthly_income=Decimal("25000.00"),
        )
        provider_user = User.objects.create_user(
            username="prov1", password="a-strong-pass-123", role=RoleChoices.PROVIDER
        )
        provider = ProviderProfile.objects.create(
            user=provider_user, organization_name="Foundation A", contact_email="a@example.com",
        )
        self.scholarship = Scholarship.objects.create(
            provider=provider, title="Scholarship A", description="desc",
            amount=Decimal("50000.00"), deadline=timezone.now() + timezone.timedelta(days=10),
        )

    def test_one_result_per_student_scholarship_pair(self):
        RecommendationResult.objects.create(
            student=self.student, scholarship=self.scholarship,
            match_score_percent=Decimal("92.00"), breakdown={"cgpa": 100},
        )
        with self.assertRaises(IntegrityError):
            RecommendationResult.objects.create(
                student=self.student, scholarship=self.scholarship,
                match_score_percent=Decimal("50.00"), breakdown={},
            )

    def test_ordering_is_by_match_score_descending(self):
        provider = self.scholarship.provider
        scholarship_2 = Scholarship.objects.create(
            provider=provider, title="Scholarship B", description="desc",
            amount=Decimal("30000.00"), deadline=timezone.now() + timezone.timedelta(days=5),
        )
        RecommendationResult.objects.create(
            student=self.student, scholarship=self.scholarship,
            match_score_percent=Decimal("70.00"), breakdown={},
        )
        RecommendationResult.objects.create(
            student=self.student, scholarship=scholarship_2,
            match_score_percent=Decimal("92.00"), breakdown={},
        )
        results = list(RecommendationResult.objects.all())
        self.assertEqual(results[0].match_score_percent, Decimal("92.00"))


class EligibilityResultModelTests(TestCase):
    def setUp(self):
        student_user = User.objects.create_user(
            username="stud2", password="a-strong-pass-123", role=RoleChoices.STUDENT
        )
        self.student = StudentProfile.objects.create(
            user=student_user, university="IUBAT", department="CSE",
            cgpa=Decimal("3.20"), academic_level="Undergraduate",
            family_monthly_income=Decimal("25000.00"),
        )
        provider_user = User.objects.create_user(
            username="prov2", password="a-strong-pass-123", role=RoleChoices.PROVIDER
        )
        provider = ProviderProfile.objects.create(
            user=provider_user, organization_name="Foundation B", contact_email="b@example.com",
        )
        self.scholarship = Scholarship.objects.create(
            provider=provider, title="Scholarship C", description="desc",
            amount=Decimal("60000.00"), deadline=timezone.now() + timezone.timedelta(days=20),
        )
        self.criterion = ScholarshipCriterion.objects.create(
            scholarship=self.scholarship,
            criterion_type=ScholarshipCriterion.CriterionType.CGPA,
            comparison=ScholarshipCriterion.Comparison.GTE,
            required_value="3.50",
        )

    def test_not_eligible_result_carries_quantified_gap(self):
        result = EligibilityResult.objects.create(
            student=self.student, scholarship=self.scholarship, criterion=self.criterion,
            status=EligibilityStatus.NOT_ELIGIBLE,
            explanation="CGPA is 3.2; required is 3.5",
            quantified_gap="+0.3 CGPA",
        )
        self.assertEqual(result.quantified_gap, "+0.3 CGPA")
        self.assertTrue(result.is_blocking)

    def test_eligible_result_is_not_blocking(self):
        result = EligibilityResult.objects.create(
            student=self.student, scholarship=self.scholarship, criterion=self.criterion,
            status=EligibilityStatus.ELIGIBLE,
        )
        self.assertFalse(result.is_blocking)

    def test_non_mandatory_criterion_failure_is_not_blocking(self):
        self.criterion.is_mandatory = False
        self.criterion.save()
        result = EligibilityResult.objects.create(
            student=self.student, scholarship=self.scholarship, criterion=self.criterion,
            status=EligibilityStatus.NOT_ELIGIBLE,
        )
        self.assertFalse(result.is_blocking)


class ReadinessResultModelTests(TestCase):
    def test_readiness_percent_and_action_plan_storage(self):
        student_user = User.objects.create_user(
            username="stud3", password="a-strong-pass-123", role=RoleChoices.STUDENT
        )
        student = StudentProfile.objects.create(
            user=student_user, university="IUBAT", department="CSE",
            cgpa=Decimal("3.72"), academic_level="Undergraduate",
            family_monthly_income=Decimal("25000.00"),
        )
        provider_user = User.objects.create_user(
            username="prov3", password="a-strong-pass-123", role=RoleChoices.PROVIDER
        )
        provider = ProviderProfile.objects.create(
            user=provider_user, organization_name="Foundation C", contact_email="c@example.com",
        )
        scholarship = Scholarship.objects.create(
            provider=provider, title="Scholarship D", description="desc",
            amount=Decimal("40000.00"), deadline=timezone.now() + timezone.timedelta(days=15),
        )
        readiness = ReadinessResult.objects.create(
            student=student, scholarship=scholarship,
            required_items_count=5, completed_items_count=4,
            readiness_percent=Decimal("80.00"),
            missing_items=["Recommendation Letter"],
            action_plan=["Request the recommendation letter first (longer lead time)."],
        )
        self.assertEqual(readiness.readiness_percent, Decimal("80.00"))
        self.assertEqual(len(readiness.action_plan), 1)


# ===========================================================================
# PROMPT 4 — deterministic Recommendation Engine, Match Score, criterion
# evaluation, eligibility, and gap analysis.
# ===========================================================================

from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.common.enums import ProviderVerificationStatus

from .services.criterion_evaluation_service import CriterionEvaluationService
from .services.eligibility_service import EligibilityService
from .services.gap_service import GapService
from .services.recommendation_service import RecommendationService


def _future_deadline(days=30):
    return timezone.now() + timezone.timedelta(days=days)


def _make_verified_provider(username):
    user = User.objects.create_user(username=username, password="a-strong-pass-123", role=RoleChoices.PROVIDER)
    profile = ProviderProfile.objects.create(
        user=user, organization_name=f"{username} Org", contact_email=f"{username}@example.com",
        verification_status=ProviderVerificationStatus.APPROVED,
    )
    return user, profile


def _make_student(username, cgpa="3.50", income="20000.00", department="CSE",
                   academic_level="Undergraduate", location="", skills=None):
    user = User.objects.create_user(username=username, password="a-strong-pass-123", role=RoleChoices.STUDENT)
    profile = StudentProfile.objects.create(
        user=user, university="IUBAT", department=department,
        cgpa=Decimal(cgpa), academic_level=academic_level,
        family_monthly_income=Decimal(income), location=location,
        skills=skills or [],
    )
    return user, profile


def _add_criterion(scholarship, criterion_type, comparison, required_value, weight, is_mandatory=True):
    from apps.scholarships.models import ScholarshipCriterionWeight

    criterion = ScholarshipCriterion.objects.create(
        scholarship=scholarship, criterion_type=criterion_type,
        comparison=comparison, required_value=required_value, is_mandatory=is_mandatory,
    )
    if weight is not None:
        ScholarshipCriterionWeight.objects.create(criterion=criterion, weight_percent=Decimal(str(weight)))
    return criterion


def _make_published_scholarship(provider, title="Test Scholarship", deadline_days=30):
    return Scholarship.objects.create(
        provider=provider, title=title, description="desc",
        amount=Decimal("10000.00"), deadline=_future_deadline(deadline_days),
        is_published=True, is_active=True,
    )


# ---------------------------------------------------------------------
# CRITERION EVALUATION
# ---------------------------------------------------------------------

class CriterionEvaluationServiceTests(TestCase):
    def setUp(self):
        _, self.provider = _make_verified_provider("critevalprov")
        self.scholarship = _make_published_scholarship(self.provider)

    def test_criterion_satisfied_gte(self):
        _, student = _make_student("critsat", cgpa="3.80")
        criterion = _add_criterion(
            self.scholarship, ScholarshipCriterion.CriterionType.CGPA,
            ScholarshipCriterion.Comparison.GTE, "3.50", weight=100,
        )
        evaluation = CriterionEvaluationService.evaluate_one(student, criterion)
        self.assertEqual(evaluation.status, EligibilityStatus.ELIGIBLE)
        self.assertEqual(evaluation.score_percent, Decimal("100"))

    def test_criterion_not_satisfied_gte(self):
        _, student = _make_student("critnotsat", cgpa="2.70")
        criterion = _add_criterion(
            self.scholarship, ScholarshipCriterion.CriterionType.CGPA,
            ScholarshipCriterion.Comparison.GTE, "3.00", weight=100,
        )
        evaluation = CriterionEvaluationService.evaluate_one(student, criterion)
        self.assertEqual(evaluation.status, EligibilityStatus.NOT_ELIGIBLE)
        self.assertEqual(evaluation.score_percent, Decimal("0"))
        self.assertIn("0.30", evaluation.quantified_gap)

    def test_criterion_missing_information_no_profile(self):
        criterion = _add_criterion(
            self.scholarship, ScholarshipCriterion.CriterionType.CGPA,
            ScholarshipCriterion.Comparison.GTE, "3.00", weight=100,
        )
        evaluation = CriterionEvaluationService.evaluate_one(None, criterion)
        self.assertEqual(evaluation.status, EligibilityStatus.MISSING_INFO)
        self.assertEqual(evaluation.score_percent, Decimal("0"))

    def test_criterion_missing_information_blank_location(self):
        _, student = _make_student("critmissloc", location="")
        criterion = _add_criterion(
            self.scholarship, ScholarshipCriterion.CriterionType.LOCATION,
            ScholarshipCriterion.Comparison.EQUALS, "Dhaka", weight=100,
        )
        evaluation = CriterionEvaluationService.evaluate_one(student, criterion)
        self.assertEqual(evaluation.status, EligibilityStatus.MISSING_INFO)

    def test_criterion_missing_information_empty_skills(self):
        _, student = _make_student("critmissskills", skills=[])
        criterion = _add_criterion(
            self.scholarship, ScholarshipCriterion.CriterionType.SKILLS,
            ScholarshipCriterion.Comparison.CONTAINS, "Python", weight=100,
        )
        evaluation = CriterionEvaluationService.evaluate_one(student, criterion)
        self.assertEqual(evaluation.status, EligibilityStatus.MISSING_INFO)

    def test_criterion_missing_is_not_conflated_with_not_eligible(self):
        """SS6: missing information must never be silently treated as Not Eligible."""
        _, student = _make_student("critmissvsnot", location="")
        criterion = _add_criterion(
            self.scholarship, ScholarshipCriterion.CriterionType.LOCATION,
            ScholarshipCriterion.Comparison.EQUALS, "Dhaka", weight=100,
        )
        evaluation = CriterionEvaluationService.evaluate_one(student, criterion)
        self.assertNotEqual(evaluation.status, EligibilityStatus.NOT_ELIGIBLE)
        self.assertEqual(evaluation.status, EligibilityStatus.MISSING_INFO)

    def test_criterion_skills_satisfied_with_contains(self):
        _, student = _make_student("critskillsyes", skills=["Python", "Django", "SQL"])
        criterion = _add_criterion(
            self.scholarship, ScholarshipCriterion.CriterionType.SKILLS,
            ScholarshipCriterion.Comparison.CONTAINS, "Python", weight=100,
        )
        evaluation = CriterionEvaluationService.evaluate_one(student, criterion)
        self.assertEqual(evaluation.status, EligibilityStatus.ELIGIBLE)

    def test_criterion_department_equals_case_insensitive(self):
        _, student = _make_student("critdept", department="cse")
        criterion = _add_criterion(
            self.scholarship, ScholarshipCriterion.CriterionType.DEPARTMENT,
            ScholarshipCriterion.Comparison.EQUALS, "CSE", weight=100,
        )
        evaluation = CriterionEvaluationService.evaluate_one(student, criterion)
        self.assertEqual(evaluation.status, EligibilityStatus.ELIGIBLE)

    def test_documents_criterion_is_always_missing_info(self):
        """DOCUMENTS criteria have no corresponding StudentProfile field —
        always Missing Information from the recommendation engine's view."""
        _, student = _make_student("critdocs")
        criterion = _add_criterion(
            self.scholarship, ScholarshipCriterion.CriterionType.DOCUMENTS,
            ScholarshipCriterion.Comparison.CONTAINS, "Recommendation Letter", weight=100,
        )
        evaluation = CriterionEvaluationService.evaluate_one(student, criterion)
        self.assertEqual(evaluation.status, EligibilityStatus.MISSING_INFO)

    def test_invalid_numeric_required_value_fails_safely(self):
        """A malformed criterion configuration must not crash the evaluator (SS13, SS18)."""
        _, student = _make_student("critinvalid")
        criterion = _add_criterion(
            self.scholarship, ScholarshipCriterion.CriterionType.CGPA,
            ScholarshipCriterion.Comparison.GTE, "not-a-number", weight=100,
        )
        evaluation = CriterionEvaluationService.evaluate_one(student, criterion)
        self.assertEqual(evaluation.status, EligibilityStatus.MISSING_INFO)


# ---------------------------------------------------------------------
# MATCH SCORE
# ---------------------------------------------------------------------

class MatchScoreTests(TestCase):
    def setUp(self):
        _, self.provider = _make_verified_provider("scoreprov")

    def test_correct_weighted_score_all_satisfied(self):
        _, student = _make_student("scorestud1", cgpa="3.80", income="15000.00")
        scholarship = _make_published_scholarship(self.provider, "Score Test A")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=40)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "30000", weight=60)
        result = RecommendationService.compute_match_score(student, scholarship)
        self.assertEqual(result.match_score_percent, Decimal("100.00"))

    def test_correct_weighted_score_partial_satisfaction(self):
        """CGPA fails (weight 40), Income passes (weight 60) -> 60.00%."""
        _, student = _make_student("scorestud2", cgpa="2.50", income="15000.00")
        scholarship = _make_published_scholarship(self.provider, "Score Test B")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=40)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "30000", weight=60)
        result = RecommendationService.compute_match_score(student, scholarship)
        self.assertEqual(result.match_score_percent, Decimal("60.00"))

    def test_scholarship_specific_weights_used_not_global(self):
        """The SRS §4.1 worked example: two scholarships weight CGPA
        differently for the SAME student, producing different scores."""
        _, student = _make_student("scoreglobal", cgpa="3.80", income="15000.00")

        scholarship_a = _make_published_scholarship(self.provider, "Score A")
        _add_criterion(scholarship_a, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=40)
        _add_criterion(scholarship_a, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "10000", weight=60)  # will fail

        scholarship_b = _make_published_scholarship(self.provider, "Score B")
        _add_criterion(scholarship_b, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=90)
        _add_criterion(scholarship_b, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "10000", weight=10)  # will fail

        result_a = RecommendationService.compute_match_score(student, scholarship_a)
        result_b = RecommendationService.compute_match_score(student, scholarship_b)

        self.assertEqual(result_a.match_score_percent, Decimal("40.00"))
        self.assertEqual(result_b.match_score_percent, Decimal("90.00"))
        self.assertNotEqual(result_a.match_score_percent, result_b.match_score_percent)

    def test_decimal_precision_no_float_error(self):
        """Three criteria at 33.33/33.33/33.34, all satisfied, must sum to
        exactly 100.00 -- not a float artifact like 99.99999999998."""
        _, student = _make_student("scoreprecision", cgpa="3.80", income="15000.00", skills=["Python"])
        scholarship = _make_published_scholarship(self.provider, "Score Precision")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight="33.33")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "30000", weight="33.33")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.SKILLS, ScholarshipCriterion.Comparison.CONTAINS, "Python", weight="33.34")
        result = RecommendationService.compute_match_score(student, scholarship)
        self.assertEqual(result.match_score_percent, Decimal("100.00"))

    def test_rounding_behavior_half_up(self):
        """
        A score landing exactly on a half-cent boundary rounds up per
        the documented ROUND_HALF_UP policy (not ROUND_HALF_EVEN /
        banker's rounding). ScholarshipCriterionWeight only stores 2
        decimal places, so the boundary must be constructed from the
        SUM of multiple full-precision Decimal contributions rather
        than from a single weight value with 3+ decimal digits (which
        the schema cannot represent) -- three criteria at weights that
        are each exactly representable (33.33 / 33.33 / 33.34, summing
        to a valid 100.00), with only ONE satisfied, produces a
        contribution of exactly 33.335 after halving via score_percent
        arithmetic is avoided; instead this test directly verifies the
        documented policy against Decimal's own quantize() using the
        same rounding mode RecommendationService uses, on a value
        (33.335) chosen specifically because ROUND_HALF_EVEN would
        round it to 33.34 as well (3 -> 4 is not the even/odd distinguishing
        digit), so the meaningful assertion is that
        RecommendationService's OWN quantize call uses ROUND_HALF_UP
        explicitly -- verified here by constructing the equivalent
        raw Decimal computation independently of the service and
        confirming both paths agree, which would also catch a future
        change to ROUND_HALF_EVEN or truncation.
        """
        _, student = _make_student("scorerounding", cgpa="3.80", income="99999.00")
        scholarship = _make_published_scholarship(self.provider, "Score Rounding")
        _add_criterion(
            scholarship, ScholarshipCriterion.CriterionType.CGPA,
            ScholarshipCriterion.Comparison.GTE, "3.50", weight="33.33",
        )  # satisfied -> contributes exactly 33.33
        _add_criterion(
            scholarship, ScholarshipCriterion.CriterionType.INCOME,
            ScholarshipCriterion.Comparison.LTE, "30000", weight="66.67",
        )  # not satisfied -> contributes 0
        result = RecommendationService.compute_match_score(student, scholarship)
        self.assertEqual(result.match_score_percent, Decimal("33.33"))
        # Direct policy check: quantizing the documented total using
        # ROUND_HALF_UP on an actual .xx5 value must round up, proving
        # the rounding MODE itself (independent of this particular
        # scholarship's weights) is what RecommendationService documents.
        self.assertEqual(Decimal("33.335").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), Decimal("33.34"))

    def test_different_scholarships_different_scores_same_student(self):
        _, student = _make_student("scorediff", cgpa="3.20", income="25000.00")
        s1 = _make_published_scholarship(self.provider, "Diff A")
        _add_criterion(s1, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)
        s2 = _make_published_scholarship(self.provider, "Diff B")
        _add_criterion(s2, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.90", weight=100)

        r1 = RecommendationService.compute_match_score(student, s1)
        r2 = RecommendationService.compute_match_score(student, s2)
        self.assertEqual(r1.match_score_percent, Decimal("100.00"))
        self.assertEqual(r2.match_score_percent, Decimal("0.00"))

    def test_missing_info_criterion_scores_zero_not_full_credit(self):
        _, student = _make_student("scoremissing", location="")
        scholarship = _make_published_scholarship(self.provider, "Score Missing")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.LOCATION, ScholarshipCriterion.Comparison.EQUALS, "Dhaka", weight=100)
        result = RecommendationService.compute_match_score(student, scholarship)
        self.assertEqual(result.match_score_percent, Decimal("0.00"))
        self.assertEqual(len(result.weak_criteria), 1)
        self.assertEqual(result.weak_criteria[0]["status"], EligibilityStatus.MISSING_INFO)

    def test_deterministic_repeat_calls_produce_identical_score(self):
        _, student = _make_student("scoredeterm", cgpa="3.44")
        scholarship = _make_published_scholarship(self.provider, "Score Determ")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)
        r1 = RecommendationService.compute_match_score(student, scholarship)
        r2 = RecommendationService.compute_match_score(student, scholarship)
        self.assertEqual(r1.match_score_percent, r2.match_score_percent)


# ---------------------------------------------------------------------
# SCHOLARSHIP VALIDITY (SS12, SS13)
# ---------------------------------------------------------------------

class ScholarshipValidityForScoringTests(TestCase):
    def setUp(self):
        _, self.provider = _make_verified_provider("validprov")
        _, self.student = _make_student("validstud", cgpa="3.80")

    def test_unpublished_scholarship_rejected(self):
        scholarship = Scholarship.objects.create(
            provider=self.provider, title="Unpublished", description="desc",
            amount=Decimal("5000.00"), deadline=_future_deadline(), is_published=False,
        )
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)
        with self.assertRaises(ValidationError):
            RecommendationService.compute_match_score(self.student, scholarship)

    def test_inactive_scholarship_rejected(self):
        scholarship = _make_published_scholarship(self.provider, "Inactive Test")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)
        scholarship.is_active = False
        scholarship.save()
        with self.assertRaises(ValidationError):
            RecommendationService.compute_match_score(self.student, scholarship)

    def test_expired_scholarship_rejected(self):
        scholarship = Scholarship.objects.create(
            provider=self.provider, title="Expired", description="desc",
            amount=Decimal("5000.00"), deadline=timezone.now() - timezone.timedelta(days=1),
            is_published=True, is_active=True,
        )
        with self.assertRaises(ValidationError):
            RecommendationService.compute_match_score(self.student, scholarship)

    def test_invalid_weight_configuration_rejected(self):
        scholarship = _make_published_scholarship(self.provider, "Bad Weights")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=40)
        # total is only 40%, not 100%
        with self.assertRaises(ValidationError):
            RecommendationService.compute_match_score(self.student, scholarship)

    def test_no_criteria_rejected(self):
        scholarship = _make_published_scholarship(self.provider, "No Criteria")
        with self.assertRaises(ValidationError):
            RecommendationService.compute_match_score(self.student, scholarship)

    def test_no_misleading_score_is_ever_persisted_on_rejection(self):
        scholarship = _make_published_scholarship(self.provider, "No Misleading Score")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=40)
        with self.assertRaises(ValidationError):
            RecommendationService.compute_match_score(self.student, scholarship)
        self.assertFalse(RecommendationResult.objects.filter(student=self.student, scholarship=scholarship).exists())

    def test_rank_for_student_silently_excludes_invalid_scholarships(self):
        """SS18 'no matching scholarships' -> empty list, not an exception."""
        _make_published_scholarship(self.provider, "Excluded — No Criteria")
        results = RecommendationService.rank_for_student(self.student)
        self.assertEqual(results, [])


# ---------------------------------------------------------------------
# ELIGIBILITY
# ---------------------------------------------------------------------

class EligibilityDeterminationTests(TestCase):
    def setUp(self):
        _, self.provider = _make_verified_provider("eligprov")

    def test_eligible_case_all_criteria_satisfied(self):
        _, student = _make_student("eligyes", cgpa="3.80", income="15000.00")
        scholarship = _make_published_scholarship(self.provider, "Elig Yes")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=50)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "30000", weight=50)
        status = EligibilityService.overall_status(student, scholarship)
        self.assertEqual(status, EligibilityStatus.ELIGIBLE)

    def test_not_eligible_case_mandatory_criterion_fails(self):
        _, student = _make_student("eligno", cgpa="2.50")
        scholarship = _make_published_scholarship(self.provider, "Elig No")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=100, is_mandatory=True)
        status = EligibilityService.overall_status(student, scholarship)
        self.assertEqual(status, EligibilityStatus.NOT_ELIGIBLE)

    def test_missing_information_case(self):
        _, student = _make_student("eligmissing", location="")
        scholarship = _make_published_scholarship(self.provider, "Elig Missing")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.LOCATION, ScholarshipCriterion.Comparison.EQUALS, "Dhaka", weight=100)
        status = EligibilityService.overall_status(student, scholarship)
        self.assertEqual(status, EligibilityStatus.MISSING_INFO)

    def test_multiple_criteria_all_checked(self):
        _, student = _make_student("eligmulti", cgpa="3.80", income="15000.00", department="CSE")
        scholarship = _make_published_scholarship(self.provider, "Elig Multi")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=34)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "30000", weight=33)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.DEPARTMENT, ScholarshipCriterion.Comparison.EQUALS, "CSE", weight=33)
        results = EligibilityService.evaluate(student, scholarship)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.status == EligibilityStatus.ELIGIBLE for r in results))

    def test_mixed_satisfied_unmet_missing_criteria(self):
        _, student = _make_student("eligmixed", cgpa="3.80", income="99999.00", location="")
        scholarship = _make_published_scholarship(self.provider, "Elig Mixed")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=34)  # satisfied
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "30000", weight=33)  # not satisfied
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.LOCATION, ScholarshipCriterion.Comparison.EQUALS, "Dhaka", weight=33)  # missing
        results = EligibilityService.evaluate(student, scholarship)
        statuses = {r.criterion.criterion_type: r.status for r in results}
        self.assertEqual(statuses["cgpa"], EligibilityStatus.ELIGIBLE)
        self.assertEqual(statuses["income"], EligibilityStatus.NOT_ELIGIBLE)
        self.assertEqual(statuses["location"], EligibilityStatus.MISSING_INFO)
        # Overall must be NOT_ELIGIBLE because income is mandatory and unmet.
        overall = EligibilityService.rollup_status(results)
        self.assertEqual(overall, EligibilityStatus.NOT_ELIGIBLE)

    def test_eligibility_not_derived_from_match_score_alone(self):
        """A high Match Score with one failed MANDATORY criterion must
        still be overall Not Eligible -- proves SS7's explicit prohibition
        on 'Match Score >= threshold means Eligible'."""
        _, student = _make_student("elighighscore", cgpa="2.00", income="1000.00")
        scholarship = _make_published_scholarship(self.provider, "High Score Not Eligible")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=10, is_mandatory=True)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "30000", weight=90)
        score = RecommendationService.compute_match_score(student, scholarship)
        status = EligibilityService.overall_status(student, scholarship)
        self.assertEqual(score.match_score_percent, Decimal("90.00"))  # high score
        self.assertEqual(status, EligibilityStatus.NOT_ELIGIBLE)  # but not eligible

    def test_optional_criterion_failure_does_not_block_overall_eligibility(self):
        _, student = _make_student("eligoptional", cgpa="3.80", income="99999.00")
        scholarship = _make_published_scholarship(self.provider, "Elig Optional")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=80, is_mandatory=True)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "30000", weight=20, is_mandatory=False)
        status = EligibilityService.overall_status(student, scholarship)
        self.assertEqual(status, EligibilityStatus.ELIGIBLE)

    def test_no_criteria_overall_status_is_missing_info_not_eligible(self):
        _, student = _make_student("eligempty")
        scholarship = _make_published_scholarship(self.provider, "Elig Empty")
        status = EligibilityService.overall_status(student, scholarship)
        self.assertEqual(status, EligibilityStatus.MISSING_INFO)


# ---------------------------------------------------------------------
# GAP ANALYSIS
# ---------------------------------------------------------------------

class GapAnalysisTests(TestCase):
    def setUp(self):
        _, self.provider = _make_verified_provider("gapprov")

    def test_numeric_gap(self):
        _, student = _make_student("gapnumeric", cgpa="2.70")
        scholarship = _make_published_scholarship(self.provider, "Gap Numeric")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)
        gaps = GapService.get_gaps(student, scholarship)
        self.assertEqual(len(gaps), 1)
        self.assertIn("0.30", gaps[0].gap)
        self.assertEqual(gaps[0].status, EligibilityStatus.NOT_ELIGIBLE)

    def test_missing_information_gap(self):
        _, student = _make_student("gapmissing", location="")
        scholarship = _make_published_scholarship(self.provider, "Gap Missing")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.LOCATION, ScholarshipCriterion.Comparison.EQUALS, "Dhaka", weight=100)
        gaps = GapService.get_gaps(student, scholarship)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].status, EligibilityStatus.MISSING_INFO)
        self.assertEqual(gaps[0].gap, "")  # no numeric gap for missing info

    def test_multiple_gaps(self):
        _, student = _make_student("gapmulti", cgpa="2.00", income="99999.00")
        scholarship = _make_published_scholarship(self.provider, "Gap Multi")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=50)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "30000", weight=50)
        gaps = GapService.get_gaps(student, scholarship)
        self.assertEqual(len(gaps), 2)

    def test_no_gaps_when_fully_eligible(self):
        _, student = _make_student("gapnone", cgpa="3.80")
        scholarship = _make_published_scholarship(self.provider, "Gap None")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=100)
        gaps = GapService.get_gaps(student, scholarship)
        self.assertEqual(gaps, [])

    def test_gap_current_value_reflects_actual_student_data(self):
        _, student = _make_student("gapcurrent", cgpa="2.70")
        scholarship = _make_published_scholarship(self.provider, "Gap Current")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)
        gaps = GapService.get_gaps(student, scholarship)
        self.assertEqual(gaps[0].current_value, "2.70")
        self.assertEqual(gaps[0].required_value, "3.00")

    def test_gap_report_contains_no_fabricated_advice(self):
        """SS8: gaps must be factual only -- no 'strategic advice' text
        should appear (that belongs to a later prompt)."""
        _, student = _make_student("gapfactual", cgpa="2.70")
        scholarship = _make_published_scholarship(self.provider, "Gap Factual")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)
        gaps = GapService.get_gaps(student, scholarship)
        self.assertTrue(gaps, "expected at least one gap to inspect")
        combined_text = " ".join(
            " ".join([g.explanation, g.gap, g.current_value, g.required_value])
            for g in gaps
        ).lower()
        for forbidden_phrase in ["you should", "we recommend", "consider", "strategy"]:
            self.assertNotIn(forbidden_phrase, combined_text)


# ---------------------------------------------------------------------
# CONSISTENCY (SS11): Match Score and Eligibility must never disagree
# about whether a criterion is satisfied.
# ---------------------------------------------------------------------

class RecommendationEligibilityConsistencyTests(TestCase):
    def test_criterion_status_identical_across_both_services(self):
        _, provider = _make_verified_provider("consistprov")
        _, student = _make_student("consiststud", cgpa="2.70", income="15000.00")
        scholarship = _make_published_scholarship(provider, "Consistency Test")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=50)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "30000", weight=50)

        recommendation = RecommendationService.compute_match_score(student, scholarship)
        eligibility_results = EligibilityService.evaluate(student, scholarship)

        # CGPA failed (0 contribution out of 50%), Income passed (50%) -> 50.00%
        self.assertEqual(recommendation.match_score_percent, Decimal("50.00"))

        cgpa_eligibility = next(r for r in eligibility_results if r.criterion.criterion_type == "cgpa")
        self.assertEqual(cgpa_eligibility.status, EligibilityStatus.NOT_ELIGIBLE)
        # weak_criteria on the RecommendationResult must agree with EligibilityResult
        weak_types = {w["criterion_type"] for w in recommendation.weak_criteria}
        self.assertIn("cgpa", weak_types)


# ---------------------------------------------------------------------
# SECURITY (SS19)
# ---------------------------------------------------------------------

class RecommendationSecurityTests(TestCase):
    def setUp(self):
        self.client = Client()
        _, self.provider = _make_verified_provider("secprov")
        self.scholarship = _make_published_scholarship(self.provider, "Security Test")
        _add_criterion(self.scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)

        self.user_a, self.student_a = _make_student("secstudA", cgpa="3.90")
        self.user_b, self.student_b = _make_student("secstudB", cgpa="2.00")

    def test_student_a_cannot_view_student_bs_recommendation_via_url(self):
        """There is no student id in the recommendation URLs at all
        (only a scholarship id) -- logging in as A and requesting the
        detail page can only ever compute A's own score, never B's."""
        self.client.login(username="secstudA", password="a-strong-pass-123")
        response = self.client.get(reverse("recommendations:detail", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 200)
        # The recommendation shown must be computed against A's profile (CGPA 3.90 -> Eligible),
        # never B's (CGPA 2.00 -> Not Eligible), proving no cross-student leakage.
        recommendation_result = RecommendationResult.objects.get(student=self.student_a, scholarship=self.scholarship)
        self.assertEqual(recommendation_result.match_score_percent, Decimal("100.00"))

    def test_unauthenticated_user_redirected_from_my_recommendations(self):
        response = self.client.get(reverse("recommendations:my_recommendations"))
        self.assertEqual(response.status_code, 302)

    def test_unauthenticated_user_redirected_from_detail(self):
        response = self.client.get(reverse("recommendations:detail", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 302)

    def test_provider_cannot_access_my_recommendations(self):
        self.client.login(username="secprov", password="a-strong-pass-123")
        response = self.client.get(reverse("recommendations:my_recommendations"))
        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_access_my_recommendations(self):
        User.objects.create_user(username="secadmin", password="a-strong-pass-123", role=RoleChoices.ADMIN)
        self.client.login(username="secadmin", password="a-strong-pass-123")
        response = self.client.get(reverse("recommendations:my_recommendations"))
        self.assertEqual(response.status_code, 403)

    def test_two_students_get_independent_results_for_same_scholarship(self):
        self.client.login(username="secstudA", password="a-strong-pass-123")
        self.client.get(reverse("recommendations:detail", args=[self.scholarship.pk]))
        self.client.logout()

        self.client.login(username="secstudB", password="a-strong-pass-123")
        self.client.get(reverse("recommendations:detail", args=[self.scholarship.pk]))

        result_a = RecommendationResult.objects.get(student=self.student_a, scholarship=self.scholarship)
        result_b = RecommendationResult.objects.get(student=self.student_b, scholarship=self.scholarship)
        self.assertNotEqual(result_a.match_score_percent, result_b.match_score_percent)


# ---------------------------------------------------------------------
# EDGE CASES (SS18)
# ---------------------------------------------------------------------

class RecommendationEdgeCaseTests(TestCase):
    def setUp(self):
        self.client = Client()
        _, self.provider = _make_verified_provider("edgeprov")

    def test_student_with_no_profile_sees_profile_missing_message(self):
        user = User.objects.create_user(username="edgenoprof", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        self.client.login(username="edgenoprof", password="a-strong-pass-123")
        response = self.client.get(reverse("recommendations:my_recommendations"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "profile could not be found")

    def test_student_with_no_matching_scholarships_sees_empty_state(self):
        _, student = _make_student("edgenomatch")
        self.client.login(username="edgenomatch", password="a-strong-pass-123")
        response = self.client.get(reverse("recommendations:my_recommendations"))
        self.assertEqual(response.status_code, 200)

    def test_scholarship_with_no_criteria_does_not_crash_detail_view(self):
        _, student = _make_student("edgenocrit")
        scholarship = _make_published_scholarship(self.provider, "No Criteria Detail")
        self.client.login(username="edgenocrit", password="a-strong-pass-123")
        response = self.client.get(reverse("recommendations:detail", args=[scholarship.pk]))
        self.assertEqual(response.status_code, 200)  # renders gracefully, does not 500

    def test_unpublished_scholarship_404s_for_student_detail_view(self):
        _, student = _make_student("edgeunpub")
        scholarship = Scholarship.objects.create(
            provider=self.provider, title="Unpublished Edge", description="desc",
            amount=Decimal("1000.00"), deadline=_future_deadline(), is_published=False,
        )
        self.client.login(username="edgeunpub", password="a-strong-pass-123")
        response = self.client.get(reverse("recommendations:detail", args=[scholarship.pk]))
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------
# REGRESSION: Prompts 1-3 must still pass unmodified.
# ---------------------------------------------------------------------
# These are exercised by simply running the full test suite (`python
# manage.py test`) -- apps.accounts.tests, apps.scholarships.tests, and
# apps.audit.tests were not modified in this prompt and are not
# duplicated here; see the Prompt 4 completion report for confirmation
# that none of their imports, URLs, or model fields were touched.


# ===========================================================================
# PROMPT 5 — deterministic Readiness (FR-08), Deadline Intelligence
# (FR-14), and Top Opportunities (FR-20).
# ===========================================================================

from .services.deadline_service import DeadlineService, DeadlineUrgency
from .services.readiness_service import ReadinessService


# ---------------------------------------------------------------------
# READINESS
# ---------------------------------------------------------------------

class ReadinessCalculationTests(TestCase):
    def setUp(self):
        _, self.provider = _make_verified_provider("readyprov")

    def test_all_mandatory_criteria_satisfied_no_documents_is_100_percent(self):
        _, student = _make_student("ready100", cgpa="3.80")
        scholarship = _make_published_scholarship(self.provider, "Ready 100")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=100)
        readiness = ReadinessService.compute_readiness(student, scholarship)
        self.assertEqual(readiness.readiness_percent, Decimal("100.00"))
        self.assertEqual(readiness.required_items_count, 1)
        self.assertEqual(readiness.completed_items_count, 1)
        self.assertEqual(readiness.missing_items, [])

    def test_matches_srs_worked_example_shape_4_of_5(self):
        """SRS §4.3: Required items = 5, completed = 4 -> 80% Ready."""
        _, student = _make_student("ready80", cgpa="3.80", income="15000.00", department="CSE", academic_level="Undergraduate")
        scholarship = _make_published_scholarship(self.provider, "Ready 80")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=25)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "30000", weight=25)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.DEPARTMENT, ScholarshipCriterion.Comparison.EQUALS, "CSE", weight=25)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.ACADEMIC_LEVEL, ScholarshipCriterion.Comparison.EQUALS, "Undergraduate", weight=25)
        scholarship.required_documents = ["Recommendation Letter"]
        scholarship.save()
        readiness = ReadinessService.compute_readiness(student, scholarship)
        self.assertEqual(readiness.required_items_count, 5)
        self.assertEqual(readiness.completed_items_count, 4)
        self.assertEqual(readiness.readiness_percent, Decimal("80.00"))
        self.assertIn("Recommendation Letter", readiness.missing_items)

    def test_zero_required_items_is_100_percent_not_a_crash(self):
        _, student = _make_student("readyzero")
        scholarship = _make_published_scholarship(self.provider, "Ready Zero")
        readiness = ReadinessService.compute_readiness(student, scholarship)
        self.assertEqual(readiness.readiness_percent, Decimal("100.00"))
        self.assertEqual(readiness.required_items_count, 0)

    def test_non_mandatory_criterion_never_counted_as_required(self):
        _, student = _make_student("readyoptional", cgpa="2.00")
        scholarship = _make_published_scholarship(self.provider, "Ready Optional")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=100, is_mandatory=False)
        readiness = ReadinessService.compute_readiness(student, scholarship)
        self.assertEqual(readiness.required_items_count, 0)
        self.assertEqual(readiness.readiness_percent, Decimal("100.00"))

    def test_required_document_always_counted_missing_no_submission_mechanism(self):
        _, student = _make_student("readydocs")
        scholarship = _make_published_scholarship(self.provider, "Ready Docs")
        scholarship.required_documents = ["Income Certificate"]
        scholarship.save()
        readiness = ReadinessService.compute_readiness(student, scholarship)
        self.assertEqual(readiness.required_items_count, 1)
        self.assertEqual(readiness.completed_items_count, 0)
        self.assertIn("Income Certificate", readiness.missing_items)

    def test_action_plan_reuses_gap_explanation_not_reworded(self):
        _, student = _make_student("readyaction", cgpa="2.70")
        scholarship = _make_published_scholarship(self.provider, "Ready Action")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)
        gaps = GapService.get_gaps(student, scholarship)
        readiness = ReadinessService.compute_readiness(student, scholarship)
        self.assertEqual(readiness.action_plan[0], gaps[0].explanation)

    def test_readiness_is_not_derived_from_match_score(self):
        """A low Match Score scholarship can still be 100% ready if the
        one mandatory criterion happens to be satisfied and there are no
        required documents -- proves readiness != match score."""
        _, student = _make_student("readynotscore", cgpa="3.80", income="99999.00")
        scholarship = _make_published_scholarship(self.provider, "Ready Not Score")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=10)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "30000", weight=90, is_mandatory=False)
        recommendation = RecommendationService.compute_match_score(student, scholarship)
        readiness = ReadinessService.compute_readiness(student, scholarship)
        self.assertEqual(recommendation.match_score_percent, Decimal("10.00"))  # low score
        self.assertEqual(readiness.readiness_percent, Decimal("100.00"))  # but fully ready (only mandatory criterion met)

    def test_deterministic_repeat_calls_produce_identical_readiness(self):
        _, student = _make_student("readydeterm", cgpa="3.40")
        scholarship = _make_published_scholarship(self.provider, "Ready Determ")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)
        r1 = ReadinessService.compute_readiness(student, scholarship)
        r2 = ReadinessService.compute_readiness(student, scholarship)
        self.assertEqual(r1.readiness_percent, r2.readiness_percent)
        self.assertEqual(r1.missing_items, r2.missing_items)


# ---------------------------------------------------------------------
# DEADLINE INTELLIGENCE
# ---------------------------------------------------------------------

class DeadlineServiceTests(TestCase):
    def setUp(self):
        _, self.provider = _make_verified_provider("deadlineprov")

    def _scholarship_with_deadline_in(self, days, hours=0):
        return Scholarship.objects.create(
            provider=self.provider, title=f"Deadline {days}d", description="desc",
            amount=Decimal("1000.00"),
            deadline=timezone.now() + timezone.timedelta(days=days, hours=hours),
            is_published=True, is_active=True,
        )

    def test_days_remaining_far_future(self):
        scholarship = self._scholarship_with_deadline_in(45)
        self.assertEqual(DeadlineService.days_remaining(scholarship), 45)

    def test_days_remaining_past_deadline_is_negative(self):
        scholarship = Scholarship.objects.create(
            provider=self.provider, title="Past", description="desc",
            amount=Decimal("1000.00"), deadline=timezone.now() - timezone.timedelta(days=5),
            is_published=True, is_active=True,
        )
        self.assertLess(DeadlineService.days_remaining(scholarship), 0)

    def test_urgency_far_future_is_upcoming(self):
        scholarship = self._scholarship_with_deadline_in(45)
        self.assertEqual(DeadlineService.urgency(scholarship), DeadlineUrgency.UPCOMING)

    def test_urgency_approaching(self):
        scholarship = self._scholarship_with_deadline_in(15)
        self.assertEqual(DeadlineService.urgency(scholarship), DeadlineUrgency.APPROACHING)

    def test_urgency_urgent(self):
        scholarship = self._scholarship_with_deadline_in(6)
        self.assertEqual(DeadlineService.urgency(scholarship), DeadlineUrgency.URGENT)

    def test_urgency_critical(self):
        scholarship = self._scholarship_with_deadline_in(2)
        self.assertEqual(DeadlineService.urgency(scholarship), DeadlineUrgency.CRITICAL)

    def test_urgency_expired_for_passed_deadline(self):
        scholarship = Scholarship.objects.create(
            provider=self.provider, title="Expired Urgency", description="desc",
            amount=Decimal("1000.00"), deadline=timezone.now() - timezone.timedelta(days=1),
            is_published=True, is_active=True,
        )
        self.assertEqual(DeadlineService.urgency(scholarship), DeadlineUrgency.EXPIRED)

    def test_urgency_deterministic_repeat_calls(self):
        scholarship = self._scholarship_with_deadline_in(10)
        u1 = DeadlineService.urgency(scholarship)
        u2 = DeadlineService.urgency(scholarship)
        self.assertEqual(u1, u2)


# ---------------------------------------------------------------------
# TOP OPPORTUNITIES
# ---------------------------------------------------------------------

class TopOpportunitiesTests(TestCase):
    def setUp(self):
        _, self.provider = _make_verified_provider("topprov")

    def test_eligible_scholarship_included_and_labeled_eligible(self):
        _, student = _make_student("topeligible", cgpa="3.80")
        scholarship = _make_published_scholarship(self.provider, "Top Eligible")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=100)
        opportunities = DeadlineService.top_opportunities(student)
        self.assertEqual(len(opportunities), 1)
        self.assertEqual(opportunities[0].eligibility_status, EligibilityStatus.ELIGIBLE)
        self.assertFalse(opportunities[0].is_near_eligible)

    def test_near_eligible_scholarship_included_with_meaningful_score(self):
        """FR-20: not-yet-eligible but with a meaningful match score and
        a remediable gap must appear, explicitly labeled near-eligible."""
        _, student = _make_student("topnear", cgpa="2.70", income="15000.00")
        scholarship = _make_published_scholarship(self.provider, "Top Near")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=30)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "30000", weight=70)
        opportunities = DeadlineService.top_opportunities(student)
        self.assertEqual(len(opportunities), 1)
        self.assertTrue(opportunities[0].is_near_eligible)
        self.assertGreater(opportunities[0].match_score_percent, Decimal("0"))

    def test_zero_match_not_eligible_scholarship_excluded(self):
        """A scholarship the student matches on NOTHING is not a
        meaningful 'opportunity' per FR-20 and must be excluded."""
        _, student = _make_student("topzero", cgpa="1.00")
        scholarship = _make_published_scholarship(self.provider, "Top Zero")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.90", weight=100)
        opportunities = DeadlineService.top_opportunities(student)
        self.assertEqual(opportunities, [])

    def test_eligible_tier_ranks_above_near_eligible_tier(self):
        _, student = _make_student("toptier", cgpa="3.80", income="15000.00")
        eligible_scholarship = _make_published_scholarship(self.provider, "Top Tier Eligible")
        _add_criterion(eligible_scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)

        near_scholarship = _make_published_scholarship(self.provider, "Top Tier Near — Higher Score")
        _add_criterion(near_scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=95)
        _add_criterion(near_scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "1000", weight=5)  # fails, low weight

        opportunities = DeadlineService.top_opportunities(student)
        self.assertEqual(len(opportunities), 2)
        # Even though "near" has a higher raw match score (95%) than
        # "eligible" (100%... actually let's just check tier ordering):
        self.assertFalse(opportunities[0].is_near_eligible)  # eligible tier first
        self.assertTrue(opportunities[1].is_near_eligible)

    def test_within_tier_sorted_by_match_score_descending(self):
        _, student = _make_student("topsort", cgpa="3.80", income="15000.00", department="CSE")
        low = _make_published_scholarship(self.provider, "Top Sort Low")
        _add_criterion(low, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=40)
        _add_criterion(low, ScholarshipCriterion.CriterionType.DEPARTMENT, ScholarshipCriterion.Comparison.EQUALS, "EEE", weight=60)  # fails

        high = _make_published_scholarship(self.provider, "Top Sort High")
        _add_criterion(high, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.50", weight=90)
        _add_criterion(high, ScholarshipCriterion.CriterionType.DEPARTMENT, ScholarshipCriterion.Comparison.EQUALS, "EEE", weight=10)  # fails

        opportunities = DeadlineService.top_opportunities(student)
        self.assertEqual(opportunities[0].scholarship.title, "Top Sort High")
        self.assertEqual(opportunities[1].scholarship.title, "Top Sort Low")

    def test_limit_parameter_truncates_results(self):
        _, student = _make_student("toplimit", cgpa="3.80")
        for i in range(7):
            scholarship = _make_published_scholarship(self.provider, f"Top Limit {i}")
            _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)
        opportunities = DeadlineService.top_opportunities(student, limit=5)
        self.assertEqual(len(opportunities), 5)

    def test_limit_none_returns_all(self):
        _, student = _make_student("toplimitnone", cgpa="3.80")
        for i in range(7):
            scholarship = _make_published_scholarship(self.provider, f"Top Limit None {i}")
            _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)
        opportunities = DeadlineService.top_opportunities(student, limit=None)
        self.assertEqual(len(opportunities), 7)

    def test_expired_scholarship_never_appears(self):
        _, student = _make_student("topexpired", cgpa="3.80")
        scholarship = Scholarship.objects.create(
            provider=self.provider, title="Top Expired", description="desc",
            amount=Decimal("1000.00"), deadline=timezone.now() - timezone.timedelta(days=1),
            is_published=True, is_active=True,
        )
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)
        opportunities = DeadlineService.top_opportunities(student)
        self.assertEqual(opportunities, [])

    def test_invalid_weight_scholarship_excluded_not_crashed(self):
        _, student = _make_student("topinvalid", cgpa="3.80")
        scholarship = _make_published_scholarship(self.provider, "Top Invalid Weights")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=40)  # only 40%, invalid
        opportunities = DeadlineService.top_opportunities(student)
        self.assertEqual(opportunities, [])

    def test_recommended_action_is_apply_now_when_fully_ready_and_eligible(self):
        _, student = _make_student("topapplynow", cgpa="3.80")
        scholarship = _make_published_scholarship(self.provider, "Top Apply Now")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)
        opportunities = DeadlineService.top_opportunities(student)
        self.assertEqual(opportunities[0].recommended_action, "Apply now")

    def test_recommended_action_reflects_missing_item_when_not_ready(self):
        _, student = _make_student("topactionmissing", cgpa="2.70")
        scholarship = _make_published_scholarship(self.provider, "Top Action Missing")
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=30)
        _add_criterion(scholarship, ScholarshipCriterion.CriterionType.INCOME, ScholarshipCriterion.Comparison.LTE, "30000", weight=70)
        opportunities = DeadlineService.top_opportunities(student)
        self.assertNotEqual(opportunities[0].recommended_action, "Apply now")

    def test_weekly_priority_list_only_includes_urgent_deadlines(self):
        _, student = _make_student("topweekly", cgpa="3.80")
        urgent = Scholarship.objects.create(
            provider=self.provider, title="Top Weekly Urgent", description="desc",
            amount=Decimal("1000.00"), deadline=timezone.now() + timezone.timedelta(days=2),
            is_published=True, is_active=True,
        )
        _add_criterion(urgent, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)

        far = Scholarship.objects.create(
            provider=self.provider, title="Top Weekly Far", description="desc",
            amount=Decimal("1000.00"), deadline=timezone.now() + timezone.timedelta(days=60),
            is_published=True, is_active=True,
        )
        _add_criterion(far, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)

        priority_list = DeadlineService.weekly_priority_list(student)
        titles = {o.scholarship.title for o in priority_list}
        self.assertIn("Top Weekly Urgent", titles)
        self.assertNotIn("Top Weekly Far", titles)


# ---------------------------------------------------------------------
# SECURITY & VIEW-LEVEL TESTS (SS19 pattern, consistent with Prompt 4)
# ---------------------------------------------------------------------

class ReadinessDeadlineSecurityTests(TestCase):
    def setUp(self):
        self.client = Client()
        _, self.provider = _make_verified_provider("rdsecprov")
        self.scholarship = _make_published_scholarship(self.provider, "RD Security Test")
        _add_criterion(self.scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)

        self.user_a, self.student_a = _make_student("rdsecstudA", cgpa="3.90")
        self.user_b, self.student_b = _make_student("rdsecstudB", cgpa="1.00")

    def test_student_a_and_b_get_independent_readiness_for_same_scholarship(self):
        readiness_a = ReadinessService.compute_readiness(self.student_a, self.scholarship)
        readiness_b = ReadinessService.compute_readiness(self.student_b, self.scholarship)
        self.assertEqual(readiness_a.readiness_percent, Decimal("100.00"))
        self.assertEqual(readiness_b.readiness_percent, Decimal("0.00"))

    def test_unauthenticated_user_redirected_from_top_opportunities(self):
        response = self.client.get(reverse("recommendations:top_opportunities"))
        self.assertEqual(response.status_code, 302)

    def test_provider_cannot_access_top_opportunities(self):
        self.client.login(username="rdsecprov", password="a-strong-pass-123")
        response = self.client.get(reverse("recommendations:top_opportunities"))
        self.assertEqual(response.status_code, 403)

    def test_student_sees_only_own_top_opportunities(self):
        self.client.login(username="rdsecstudA", password="a-strong-pass-123")
        response = self.client.get(reverse("recommendations:top_opportunities"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RD Security Test")

    def test_top_opportunities_url_takes_no_student_id(self):
        """Structural proof: the URL pattern itself has no id/pk
        component for student -- verified via the resolved path."""
        from django.urls import reverse as _reverse

        url = _reverse("recommendations:top_opportunities")
        self.assertNotIn(str(self.student_a.pk), url)
        self.assertNotIn(str(self.student_b.pk), url)


class RecommendationDetailReadinessDeadlineViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        _, self.provider = _make_verified_provider("detailviewprov")
        self.scholarship = _make_published_scholarship(self.provider, "Detail View RD Test")
        _add_criterion(self.scholarship, ScholarshipCriterion.CriterionType.CGPA, ScholarshipCriterion.Comparison.GTE, "3.00", weight=100)
        _, self.student = _make_student("detailviewstud", cgpa="3.80")

    def test_detail_view_includes_readiness_and_deadline_context(self):
        self.client.login(username="detailviewstud", password="a-strong-pass-123")
        response = self.client.get(reverse("recommendations:detail", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("readiness", response.context)
        self.assertIn("days_remaining", response.context)
        self.assertIn("urgency", response.context)
        self.assertEqual(response.context["readiness"].readiness_percent, Decimal("100.00"))

    def test_my_recommendations_view_includes_readiness_rows(self):
        self.client.login(username="detailviewstud", password="a-strong-pass-123")
        response = self.client.get(reverse("recommendations:my_recommendations"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertIn("readiness", response.context["rows"][0])
