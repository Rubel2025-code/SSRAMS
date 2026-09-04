"""
apps/scholarships/tests.py

Foundation tests for the scholarship-specific weighting model — the
central v3.1 design decision (see models.py docstring). Full scoring
logic (FR-06) is implemented and tested in apps.recommendations in a
later prompt; these tests only verify the data model correctly
represents and validates "weights must sum to 100% per scholarship".
"""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import ProviderProfile, User
from apps.common.enums import RoleChoices

from .models import Scholarship, ScholarshipCriterion, ScholarshipCriterionWeight


class ScholarshipWeightValidationTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            username="provider1", password="a-strong-pass-123", role=RoleChoices.PROVIDER
        )
        self.provider = ProviderProfile.objects.create(
            user=user, organization_name="Test Foundation", contact_email="ops@test.example",
        )
        self.scholarship = Scholarship.objects.create(
            provider=self.provider,
            title="Test Scholarship A",
            description="A scholarship for testing.",
            amount=Decimal("50000.00"),
            deadline=timezone.now() + timezone.timedelta(days=30),
        )

    def _add_criterion(self, criterion_type, weight):
        criterion = ScholarshipCriterion.objects.create(
            scholarship=self.scholarship,
            criterion_type=criterion_type,
            comparison=ScholarshipCriterion.Comparison.GTE,
            required_value="3.50",
        )
        if weight is not None:
            ScholarshipCriterionWeight.objects.create(
                criterion=criterion, weight_percent=Decimal(str(weight))
            )
        return criterion

    def test_scholarship_with_no_criteria_is_invalid(self):
        self.assertFalse(self.scholarship.weights_are_valid())

    def test_scholarship_with_criterion_missing_weight_is_invalid(self):
        self._add_criterion(ScholarshipCriterion.CriterionType.CGPA, weight=None)
        self.assertFalse(self.scholarship.weights_are_valid())

    def test_scholarship_with_weights_summing_below_100_is_invalid(self):
        self._add_criterion(ScholarshipCriterion.CriterionType.CGPA, 40)
        self._add_criterion(ScholarshipCriterion.CriterionType.INCOME, 30)
        self.assertEqual(self.scholarship.total_weight_percent, Decimal("70"))
        self.assertFalse(self.scholarship.weights_are_valid())

    def test_scholarship_with_weights_summing_to_exactly_100_is_valid(self):
        self._add_criterion(ScholarshipCriterion.CriterionType.CGPA, 40)
        self._add_criterion(ScholarshipCriterion.CriterionType.DEPARTMENT, 30)
        self._add_criterion(ScholarshipCriterion.CriterionType.INCOME, 20)
        self._add_criterion(ScholarshipCriterion.CriterionType.SKILLS, 10)
        self.assertEqual(self.scholarship.total_weight_percent, Decimal("100"))
        self.assertTrue(self.scholarship.weights_are_valid())

    def test_scholarship_with_weights_summing_above_100_is_invalid(self):
        self._add_criterion(ScholarshipCriterion.CriterionType.CGPA, 70)
        self._add_criterion(ScholarshipCriterion.CriterionType.INCOME, 40)
        self.assertEqual(self.scholarship.total_weight_percent, Decimal("110"))
        self.assertFalse(self.scholarship.weights_are_valid())

    def test_two_scholarships_can_weight_the_same_criterion_type_differently(self):
        """Proves there is no global/shared weighting — this is the core
        v3.1 requirement (FR-04, FR-06, SRS §2.4)."""
        self._add_criterion(ScholarshipCriterion.CriterionType.CGPA, 40)

        scholarship_b = Scholarship.objects.create(
            provider=self.provider,
            title="Test Scholarship B",
            description="A second scholarship with different weights.",
            amount=Decimal("35000.00"),
            deadline=timezone.now() + timezone.timedelta(days=30),
        )
        criterion_b = ScholarshipCriterion.objects.create(
            scholarship=scholarship_b,
            criterion_type=ScholarshipCriterion.CriterionType.CGPA,
            comparison=ScholarshipCriterion.Comparison.GTE,
            required_value="3.00",
        )
        ScholarshipCriterionWeight.objects.create(criterion=criterion_b, weight_percent=Decimal("20"))

        cgpa_weight_a = self.scholarship.criteria.get(
            criterion_type=ScholarshipCriterion.CriterionType.CGPA
        ).weight.weight_percent
        cgpa_weight_b = scholarship_b.criteria.get(
            criterion_type=ScholarshipCriterion.CriterionType.CGPA
        ).weight.weight_percent

        self.assertEqual(cgpa_weight_a, Decimal("40"))
        self.assertEqual(cgpa_weight_b, Decimal("20"))
        self.assertNotEqual(cgpa_weight_a, cgpa_weight_b)

    def test_scholarship_defaults_to_unpublished(self):
        self.assertFalse(self.scholarship.is_published)

    def test_scholarship_str_is_title(self):
        self.assertEqual(str(self.scholarship), "Test Scholarship A")


# ===========================================================================
# PROMPT 3 — provider CRUD, ownership enforcement, criteria/weight
# management, publication gate, student discovery, admin moderation,
# and audit logging.
# ===========================================================================

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.common.enums import ProviderVerificationStatus

from .services.criteria_service import CriteriaService
from .services.publication_service import PublicationService
from .services.scholarship_service import ScholarshipService
from .services.weight_service import WeightService


def _make_provider(username, verified=True):
    user = User.objects.create_user(username=username, password="a-strong-pass-123", role=RoleChoices.PROVIDER)
    profile = ProviderProfile.objects.create(
        user=user, organization_name=f"{username} Org", contact_email=f"{username}@example.com",
        verification_status=ProviderVerificationStatus.APPROVED if verified else ProviderVerificationStatus.PENDING,
    )
    return user, profile


def _make_student(username):
    from apps.accounts.models import StudentProfile

    user = User.objects.create_user(username=username, password="a-strong-pass-123", role=RoleChoices.STUDENT)
    profile = StudentProfile.objects.create(
        user=user, university="IUBAT", department="CSE",
        cgpa=Decimal("3.50"), academic_level="Undergraduate",
        family_monthly_income=Decimal("20000.00"),
    )
    return user, profile


def _future_deadline(days=30):
    return timezone.now() + timezone.timedelta(days=days)


class ScholarshipModelLifecycleTests(TestCase):
    """New Prompt 3 model surface: is_active, is_expired, is_visible_to_students, can_be_published."""

    def setUp(self):
        self.provider_user, self.provider = _make_provider("lifeprov")
        self.scholarship = Scholarship.objects.create(
            provider=self.provider, title="Lifecycle Test", description="desc",
            amount=Decimal("10000.00"), deadline=_future_deadline(),
        )

    def test_new_scholarship_is_active_by_default(self):
        self.assertTrue(self.scholarship.is_active)

    def test_is_expired_false_for_future_deadline(self):
        self.assertFalse(self.scholarship.is_expired)

    def test_is_expired_true_for_past_deadline(self):
        self.scholarship.deadline = timezone.now() - timezone.timedelta(days=1)
        self.scholarship.save()
        self.assertTrue(self.scholarship.is_expired)

    def test_is_visible_to_students_requires_published_active_and_not_expired(self):
        self.assertFalse(self.scholarship.is_visible_to_students)  # not published yet
        self.scholarship.is_published = True
        self.scholarship.save()
        self.assertTrue(self.scholarship.is_visible_to_students)
        self.scholarship.is_active = False
        self.scholarship.save()
        self.assertFalse(self.scholarship.is_visible_to_students)

    def test_can_be_published_requires_weights_and_verified_provider(self):
        self.assertFalse(self.scholarship.can_be_published())  # no weights
        criterion = ScholarshipCriterion.objects.create(
            scholarship=self.scholarship, criterion_type=ScholarshipCriterion.CriterionType.CGPA,
            comparison=ScholarshipCriterion.Comparison.GTE, required_value="3.00",
        )
        ScholarshipCriterionWeight.objects.create(criterion=criterion, weight_percent=Decimal("100"))
        self.assertTrue(self.scholarship.can_be_published())  # verified provider + valid weights

    def test_can_be_published_false_for_unverified_provider(self):
        unverified_user, unverified_provider = _make_provider("unverifprov", verified=False)
        scholarship = Scholarship.objects.create(
            provider=unverified_provider, title="Unverified Test", description="desc",
            amount=Decimal("5000.00"), deadline=_future_deadline(),
        )
        criterion = ScholarshipCriterion.objects.create(
            scholarship=scholarship, criterion_type=ScholarshipCriterion.CriterionType.CGPA,
            comparison=ScholarshipCriterion.Comparison.GTE, required_value="3.00",
        )
        ScholarshipCriterionWeight.objects.create(criterion=criterion, weight_percent=Decimal("100"))
        self.assertFalse(scholarship.can_be_published())

    def test_required_documents_and_instructions_default_sensibly(self):
        self.assertEqual(self.scholarship.required_documents, [])
        self.assertEqual(self.scholarship.application_instructions, "")


class WeightServiceValidationTests(TestCase):
    """Exhaustive coverage of WeightService's Decimal-based validation (project brief SS7)."""

    def setUp(self):
        self.provider_user, self.provider = _make_provider("wsprov")
        self.scholarship = Scholarship.objects.create(
            provider=self.provider, title="Weight Service Test", description="desc",
            amount=Decimal("10000.00"), deadline=_future_deadline(),
        )

    def _criterion(self, weight=None):
        c = ScholarshipCriterion.objects.create(
            scholarship=self.scholarship, criterion_type=ScholarshipCriterion.CriterionType.CGPA,
            comparison=ScholarshipCriterion.Comparison.GTE, required_value="3.00",
        )
        if weight is not None:
            ScholarshipCriterionWeight.objects.create(criterion=c, weight_percent=Decimal(str(weight)))
        return c

    def test_no_criteria_is_invalid_with_explanatory_issue(self):
        result = WeightService.validate_configuration(self.scholarship)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("no eligibility criteria" in issue for issue in result.issues))

    def test_missing_weight_is_invalid_with_explanatory_issue(self):
        self._criterion(weight=None)
        result = WeightService.validate_configuration(self.scholarship)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("no weight assigned" in issue for issue in result.issues))

    def test_negative_weight_rejected_at_set_time(self):
        criterion = self._criterion()
        with self.assertRaises(ValidationError):
            WeightService.set_weight(criterion=criterion, weight_percent="-10", actor=self.provider_user)

    def test_weight_over_100_rejected_at_set_time(self):
        criterion = self._criterion()
        with self.assertRaises(ValidationError):
            WeightService.set_weight(criterion=criterion, weight_percent="150", actor=self.provider_user)

    def test_non_decimal_weight_rejected(self):
        criterion = self._criterion()
        with self.assertRaises(ValidationError):
            WeightService.set_weight(criterion=criterion, weight_percent="not-a-number", actor=self.provider_user)

    def test_zero_weight_is_permitted_for_a_single_criterion(self):
        criterion = self._criterion()
        weight = WeightService.set_weight(criterion=criterion, weight_percent="0", actor=self.provider_user)
        self.assertEqual(weight.weight_percent, Decimal("0"))

    def test_total_below_100_is_invalid(self):
        self._criterion(weight=40)
        result = WeightService.validate_configuration(self.scholarship)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.total, Decimal("40"))

    def test_total_above_100_is_invalid(self):
        c1 = self._criterion(weight=70)
        c2 = ScholarshipCriterion.objects.create(
            scholarship=self.scholarship, criterion_type=ScholarshipCriterion.CriterionType.INCOME,
            comparison=ScholarshipCriterion.Comparison.LTE, required_value="30000",
        )
        ScholarshipCriterionWeight.objects.create(criterion=c2, weight_percent=Decimal("50"))
        result = WeightService.validate_configuration(self.scholarship)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.total, Decimal("120"))

    def test_total_exactly_100_is_valid(self):
        self._criterion(weight=100)
        result = WeightService.validate_configuration(self.scholarship)
        self.assertTrue(result.is_valid)

    def test_decimal_precision_not_float_rounding_error(self):
        """Three criteria at 33.33/33.33/33.34 should sum to exactly 100.00
        with Decimal -- a float implementation could produce 99.99999999998."""
        c1 = self._criterion(weight="33.33")
        c2 = ScholarshipCriterion.objects.create(
            scholarship=self.scholarship, criterion_type=ScholarshipCriterion.CriterionType.INCOME,
            comparison=ScholarshipCriterion.Comparison.LTE, required_value="30000",
        )
        ScholarshipCriterionWeight.objects.create(criterion=c2, weight_percent=Decimal("33.33"))
        c3 = ScholarshipCriterion.objects.create(
            scholarship=self.scholarship, criterion_type=ScholarshipCriterion.CriterionType.SKILLS,
            comparison=ScholarshipCriterion.Comparison.CONTAINS, required_value="Python",
        )
        ScholarshipCriterionWeight.objects.create(criterion=c3, weight_percent=Decimal("33.34"))
        result = WeightService.validate_configuration(self.scholarship)
        self.assertEqual(result.total, Decimal("100.00"))
        self.assertTrue(result.is_valid)

    def test_bulk_set_rejects_criterion_id_not_belonging_to_scholarship(self):
        other_provider_user, other_provider = _make_provider("otherwsprov")
        other_scholarship = Scholarship.objects.create(
            provider=other_provider, title="Other", description="desc",
            amount=Decimal("1000.00"), deadline=_future_deadline(),
        )
        foreign_criterion = ScholarshipCriterion.objects.create(
            scholarship=other_scholarship, criterion_type=ScholarshipCriterion.CriterionType.CGPA,
            comparison=ScholarshipCriterion.Comparison.GTE, required_value="3.00",
        )
        with self.assertRaises(ValidationError):
            WeightService.set_weights_bulk(
                scholarship=self.scholarship,
                weights_by_criterion_id={str(foreign_criterion.pk): "50"},
                actor=self.provider_user,
            )

    def test_bulk_set_is_atomic_one_bad_value_saves_nothing(self):
        c1 = self._criterion(weight=40)
        c2 = ScholarshipCriterion.objects.create(
            scholarship=self.scholarship, criterion_type=ScholarshipCriterion.CriterionType.INCOME,
            comparison=ScholarshipCriterion.Comparison.LTE, required_value="30000",
        )
        with self.assertRaises(ValidationError):
            WeightService.set_weights_bulk(
                scholarship=self.scholarship,
                weights_by_criterion_id={str(c1.pk): "60", str(c2.pk): "not-a-number"},
                actor=self.provider_user,
            )
        c1.refresh_from_db()
        self.assertEqual(c1.weight.weight_percent, Decimal("40"))  # unchanged
        self.assertFalse(hasattr(c2, "weight") and ScholarshipCriterionWeight.objects.filter(criterion=c2).exists())


class PublicationServiceTests(TestCase):
    def setUp(self):
        self.provider_user, self.provider = _make_provider("pubprov")
        self.scholarship = Scholarship.objects.create(
            provider=self.provider, title="Publish Test", description="desc",
            amount=Decimal("10000.00"), deadline=_future_deadline(),
        )

    def test_publish_fails_without_valid_weights(self):
        with self.assertRaises(ValidationError):
            PublicationService.publish(scholarship=self.scholarship, actor=self.provider_user)
        self.assertFalse(self.scholarship.is_published)

    def test_publish_fails_for_unverified_provider_even_with_valid_weights(self):
        unverified_user, unverified_provider = _make_provider("unverifpub", verified=False)
        scholarship = Scholarship.objects.create(
            provider=unverified_provider, title="Unverified Publish", description="desc",
            amount=Decimal("5000.00"), deadline=_future_deadline(),
        )
        criterion = ScholarshipCriterion.objects.create(
            scholarship=scholarship, criterion_type=ScholarshipCriterion.CriterionType.CGPA,
            comparison=ScholarshipCriterion.Comparison.GTE, required_value="3.00",
        )
        ScholarshipCriterionWeight.objects.create(criterion=criterion, weight_percent=Decimal("100"))
        with self.assertRaises(PermissionDenied):
            PublicationService.publish(scholarship=scholarship, actor=unverified_user)

    def test_publish_succeeds_with_valid_weights_and_verified_provider(self):
        criterion = ScholarshipCriterion.objects.create(
            scholarship=self.scholarship, criterion_type=ScholarshipCriterion.CriterionType.CGPA,
            comparison=ScholarshipCriterion.Comparison.GTE, required_value="3.00",
        )
        ScholarshipCriterionWeight.objects.create(criterion=criterion, weight_percent=Decimal("100"))
        PublicationService.publish(scholarship=self.scholarship, actor=self.provider_user)
        self.scholarship.refresh_from_db()
        self.assertTrue(self.scholarship.is_published)
        self.assertTrue(
            AuditLog.objects.filter(event_type=AuditLog.EventType.SCHOLARSHIP_PUBLISHED).exists()
        )

    def test_unpublish_always_succeeds_for_owner(self):
        self.scholarship.is_published = True
        self.scholarship.save()
        PublicationService.unpublish(scholarship=self.scholarship, actor=self.provider_user)
        self.scholarship.refresh_from_db()
        self.assertFalse(self.scholarship.is_published)


class ScholarshipServiceOwnershipTests(TestCase):
    def setUp(self):
        self.provider_a_user, self.provider_a = _make_provider("ownerA")
        self.provider_b_user, self.provider_b = _make_provider("ownerB")
        self.scholarship = Scholarship.objects.create(
            provider=self.provider_a, title="Owned By A", description="desc",
            amount=Decimal("10000.00"), deadline=_future_deadline(),
        )

    def test_owner_can_fetch_own_scholarship(self):
        result = ScholarshipService.get_owned_scholarship_or_403(self.scholarship.pk, self.provider_a)
        self.assertEqual(result, self.scholarship)

    def test_non_owner_gets_permission_denied(self):
        with self.assertRaises(PermissionDenied):
            ScholarshipService.get_owned_scholarship_or_403(self.scholarship.pk, self.provider_b)

    def test_delete_published_scholarship_is_rejected(self):
        self.scholarship.is_published = True
        self.scholarship.save()
        with self.assertRaises(PermissionDenied):
            ScholarshipService.delete_scholarship(scholarship=self.scholarship, actor=self.provider_a_user)
        self.assertTrue(Scholarship.objects.filter(pk=self.scholarship.pk).exists())

    def test_delete_draft_scholarship_succeeds(self):
        ScholarshipService.delete_scholarship(scholarship=self.scholarship, actor=self.provider_a_user)
        self.assertFalse(Scholarship.objects.filter(pk=self.scholarship.pk).exists())


class CriteriaServiceValidationTests(TestCase):
    def setUp(self):
        self.provider_user, self.provider = _make_provider("critprov")
        self.scholarship = Scholarship.objects.create(
            provider=self.provider, title="Criteria Test", description="desc",
            amount=Decimal("10000.00"), deadline=_future_deadline(),
        )

    def test_cgpa_criterion_with_non_numeric_value_and_gte_is_rejected(self):
        with self.assertRaises(ValidationError):
            CriteriaService.add_criterion(
                scholarship=self.scholarship, actor=self.provider_user,
                criterion_type=ScholarshipCriterion.CriterionType.CGPA,
                comparison=ScholarshipCriterion.Comparison.GTE,
                required_value="not-a-number",
            )

    def test_cgpa_criterion_with_numeric_value_succeeds(self):
        criterion = CriteriaService.add_criterion(
            scholarship=self.scholarship, actor=self.provider_user,
            criterion_type=ScholarshipCriterion.CriterionType.CGPA,
            comparison=ScholarshipCriterion.Comparison.GTE,
            required_value="3.50",
        )
        self.assertEqual(criterion.required_value, "3.50")

    def test_empty_required_value_is_rejected(self):
        with self.assertRaises(ValidationError):
            CriteriaService.add_criterion(
                scholarship=self.scholarship, actor=self.provider_user,
                criterion_type=ScholarshipCriterion.CriterionType.DEPARTMENT,
                comparison=ScholarshipCriterion.Comparison.EQUALS,
                required_value="   ",
            )

    def test_duplicate_builtin_criterion_type_rejected_by_model_constraint(self):
        CriteriaService.add_criterion(
            scholarship=self.scholarship, actor=self.provider_user,
            criterion_type=ScholarshipCriterion.CriterionType.CGPA,
            comparison=ScholarshipCriterion.Comparison.GTE, required_value="3.00",
        )
        with self.assertRaises(ValidationError):
            CriteriaService.add_criterion(
                scholarship=self.scholarship, actor=self.provider_user,
                criterion_type=ScholarshipCriterion.CriterionType.CGPA,
                comparison=ScholarshipCriterion.Comparison.GTE, required_value="3.50",
            )

    def test_deleting_criterion_also_removes_its_weight(self):
        criterion = CriteriaService.add_criterion(
            scholarship=self.scholarship, actor=self.provider_user,
            criterion_type=ScholarshipCriterion.CriterionType.CGPA,
            comparison=ScholarshipCriterion.Comparison.GTE, required_value="3.00",
        )
        ScholarshipCriterionWeight.objects.create(criterion=criterion, weight_percent=Decimal("100"))
        weight_id = criterion.weight.pk
        CriteriaService.delete_criterion(criterion=criterion, actor=self.provider_user)
        self.assertFalse(ScholarshipCriterionWeight.objects.filter(pk=weight_id).exists())


class ProviderViewRBACAndOwnershipTests(TestCase):
    """HTTP-level: verified-only access, cross-provider ownership denial,
    and role-based denial for students/admins on provider routes."""

    def setUp(self):
        self.client = Client()
        self.verified_user, self.verified_provider = _make_provider("httpverified")
        self.unverified_user, self.unverified_provider = _make_provider("httpunverified", verified=False)
        self.other_verified_user, self.other_verified_provider = _make_provider("httpother")
        self.student_user, self.student_profile = _make_student("httpstudent")

        self.scholarship = Scholarship.objects.create(
            provider=self.verified_provider, title="HTTP Test Scholarship", description="desc",
            amount=Decimal("15000.00"), deadline=_future_deadline(),
        )

    def test_unverified_provider_redirected_from_create(self):
        self.client.login(username="httpunverified", password="a-strong-pass-123")
        response = self.client.get(reverse("scholarships:provider_create"))
        self.assertEqual(response.status_code, 302)

    def test_verified_provider_can_access_create(self):
        self.client.login(username="httpverified", password="a-strong-pass-123")
        response = self.client.get(reverse("scholarships:provider_create"))
        self.assertEqual(response.status_code, 200)

    def test_other_provider_cannot_edit_scholarship_they_dont_own(self):
        self.client.login(username="httpother", password="a-strong-pass-123")
        response = self.client.get(reverse("scholarships:provider_edit", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 403)

    def test_other_provider_cannot_manage_criteria_they_dont_own(self):
        self.client.login(username="httpother", password="a-strong-pass-123")
        response = self.client.get(reverse("scholarships:provider_criteria", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 403)

    def test_other_provider_cannot_manage_weights_they_dont_own(self):
        self.client.login(username="httpother", password="a-strong-pass-123")
        response = self.client.get(reverse("scholarships:provider_weights", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 403)

    def test_other_provider_cannot_publish_scholarship_they_dont_own(self):
        self.client.login(username="httpother", password="a-strong-pass-123")
        response = self.client.post(reverse("scholarships:provider_publish", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 403)
        self.scholarship.refresh_from_db()
        self.assertFalse(self.scholarship.is_published)

    def test_other_provider_cannot_delete_scholarship_they_dont_own(self):
        self.client.login(username="httpother", password="a-strong-pass-123")
        response = self.client.post(reverse("scholarships:provider_delete", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Scholarship.objects.filter(pk=self.scholarship.pk).exists())

    def test_student_cannot_access_any_provider_scholarship_route(self):
        self.client.login(username="httpstudent", password="a-strong-pass-123")
        for name, args in [
            ("scholarships:provider_create", []),
            ("scholarships:provider_detail", [self.scholarship.pk]),
            ("scholarships:provider_criteria", [self.scholarship.pk]),
            ("scholarships:provider_weights", [self.scholarship.pk]),
        ]:
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 403, f"{name} should deny students")

    def test_owner_can_view_their_own_scholarship_detail(self):
        self.client.login(username="httpverified", password="a-strong-pass-123")
        response = self.client.get(reverse("scholarships:provider_detail", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 200)


class StudentDiscoveryViewTests(TestCase):
    """FR-05: students only ever see published+active+non-expired scholarships."""

    def setUp(self):
        self.client = Client()
        self.student_user, self.student_profile = _make_student("discstudent")
        self.provider_user, self.provider = _make_provider("discprov")

        self.published = self._make_scholarship("Published & Visible", published=True, active=True)
        self.draft = self._make_scholarship("Still Draft", published=False, active=True)
        self.deactivated = self._make_scholarship("Deactivated", published=True, active=False)
        self.expired = self._make_scholarship("Expired", published=True, active=True, deadline_days=-5)

    def _make_scholarship(self, title, published, active, deadline_days=30):
        s = Scholarship.objects.create(
            provider=self.provider, title=title, description="desc",
            amount=Decimal("10000.00"), deadline=_future_deadline(deadline_days),
            is_published=published, is_active=active,
        )
        return s

    def test_catalog_shows_only_fully_visible_scholarships(self):
        self.client.login(username="discstudent", password="a-strong-pass-123")
        response = self.client.get(reverse("scholarships:list"))
        # html=True so the ampersand in the fixture title is compared after
        # HTML parsing: Django correctly renders it escaped ("Published
        # &amp; Visible"), which a raw-string assertContains would miss.
        self.assertContains(response, "Published & Visible", html=True)
        self.assertNotContains(response, "Still Draft")
        self.assertNotContains(response, "Deactivated")
        self.assertNotContains(response, "Expired")

    def test_student_cannot_view_detail_of_unpublished_scholarship(self):
        self.client.login(username="discstudent", password="a-strong-pass-123")
        response = self.client.get(reverse("scholarships:detail", args=[self.draft.pk]))
        self.assertEqual(response.status_code, 404)

    def test_student_can_view_detail_of_visible_scholarship(self):
        self.client.login(username="discstudent", password="a-strong-pass-123")
        response = self.client.get(reverse("scholarships:detail", args=[self.published.pk]))
        self.assertEqual(response.status_code, 200)

    def test_provider_list_view_shows_own_scholarships_regardless_of_status(self):
        """Providers see their own drafts/deactivated scholarships too --
        only students are restricted to is_visible_to_students."""
        self.client.login(username="discprov", password="a-strong-pass-123")
        response = self.client.get(reverse("scholarships:list"))
        self.assertContains(response, "Still Draft")
        self.assertContains(response, "Deactivated")

    def test_search_filters_by_title(self):
        self.client.login(username="discstudent", password="a-strong-pass-123")
        response = self.client.get(reverse("scholarships:list"), {"q": "Published"})
        self.assertContains(response, "Published & Visible", html=True)


class AdminModerationViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_user(username="modadmin", password="a-strong-pass-123", role=RoleChoices.ADMIN)
        self.provider_user, self.provider = _make_provider("modprov")
        self.scholarship = Scholarship.objects.create(
            provider=self.provider, title="Moderation Target", description="desc",
            amount=Decimal("10000.00"), deadline=_future_deadline(), is_published=True,
        )

    def test_admin_can_view_any_scholarship_detail(self):
        self.client.login(username="modadmin", password="a-strong-pass-123")
        response = self.client.get(reverse("scholarships:admin_detail", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 200)

    def test_non_admin_cannot_access_admin_detail(self):
        self.client.login(username="modprov", password="a-strong-pass-123")
        response = self.client.get(reverse("scholarships:admin_detail", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_deactivate_any_scholarship(self):
        self.client.login(username="modadmin", password="a-strong-pass-123")
        response = self.client.post(
            reverse("scholarships:admin_moderate", args=[self.scholarship.pk]),
            {"reason": "Policy violation"},
        )
        self.assertEqual(response.status_code, 302)
        self.scholarship.refresh_from_db()
        self.assertFalse(self.scholarship.is_active)
        entry = AuditLog.objects.filter(event_type=AuditLog.EventType.SCHOLARSHIP_MODERATED).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.admin_user)

    def test_provider_cannot_moderate_own_scholarship_via_admin_route(self):
        self.client.login(username="modprov", password="a-strong-pass-123")
        response = self.client.post(reverse("scholarships:admin_moderate", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 403)
        self.scholarship.refresh_from_db()
        self.assertTrue(self.scholarship.is_active)


class AuditLoggingScholarshipTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.provider_user, self.provider = _make_provider("auditscholprov")

    def test_creating_scholarship_via_view_logs_audit_event(self):
        self.client.login(username="auditscholprov", password="a-strong-pass-123")
        self.client.post(
            reverse("scholarships:provider_create"),
            {
                "title": "Audited Scholarship", "description": "desc",
                "amount": "5000.00",
                "deadline": (_future_deadline()).strftime("%Y-%m-%dT%H:%M"),
                "application_instructions": "", "required_documents": "",
            },
        )
        self.assertTrue(AuditLog.objects.filter(event_type=AuditLog.EventType.SCHOLARSHIP_CREATED).exists())

    def test_adding_criterion_logs_audit_event(self):
        scholarship = Scholarship.objects.create(
            provider=self.provider, title="Audit Criterion Test", description="desc",
            amount=Decimal("5000.00"), deadline=_future_deadline(),
        )
        self.client.login(username="auditscholprov", password="a-strong-pass-123")
        self.client.post(
            reverse("scholarships:provider_criteria", args=[scholarship.pk]),
            {"criterion_type": "cgpa", "comparison": "gte", "required_value": "3.00", "is_mandatory": "on"},
        )
        self.assertTrue(AuditLog.objects.filter(event_type=AuditLog.EventType.CRITERION_CREATED).exists())

    def test_setting_weights_logs_audit_event(self):
        scholarship = Scholarship.objects.create(
            provider=self.provider, title="Audit Weight Test", description="desc",
            amount=Decimal("5000.00"), deadline=_future_deadline(),
        )
        criterion = ScholarshipCriterion.objects.create(
            scholarship=scholarship, criterion_type=ScholarshipCriterion.CriterionType.CGPA,
            comparison=ScholarshipCriterion.Comparison.GTE, required_value="3.00",
        )
        self.client.login(username="auditscholprov", password="a-strong-pass-123")
        self.client.post(
            reverse("scholarships:provider_weights", args=[scholarship.pk]),
            {f"weight_{criterion.pk}": "100"},
        )
        self.assertTrue(AuditLog.objects.filter(event_type=AuditLog.EventType.WEIGHT_CHANGED).exists())
