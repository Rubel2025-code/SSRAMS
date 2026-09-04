"""apps/applications/tests.py — foundation model tests (FR-09)."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import ProviderProfile, StudentProfile, User
from apps.common.enums import ApplicationStatus, RoleChoices
from apps.scholarships.models import Scholarship

from .models import Application, ApplicationStatusHistory, Bookmark


class ApplicationModelTests(TestCase):
    def setUp(self):
        student_user = User.objects.create_user(
            username="applicant1", password="a-strong-pass-123", role=RoleChoices.STUDENT
        )
        self.student = StudentProfile.objects.create(
            user=student_user, university="IUBAT", department="CSE",
            cgpa=Decimal("3.72"), academic_level="Undergraduate",
            family_monthly_income=Decimal("25000.00"),
        )
        provider_user = User.objects.create_user(
            username="prov4", password="a-strong-pass-123", role=RoleChoices.PROVIDER
        )
        provider = ProviderProfile.objects.create(
            user=provider_user, organization_name="Foundation D", contact_email="d@example.com",
        )
        self.scholarship = Scholarship.objects.create(
            provider=provider, title="Scholarship E", description="desc",
            amount=Decimal("45000.00"), deadline=timezone.now() + timezone.timedelta(days=12),
        )

    def test_application_defaults_to_draft(self):
        application = Application.objects.create(student=self.student, scholarship=self.scholarship)
        self.assertEqual(application.status, ApplicationStatus.DRAFT)
        self.assertIsNone(application.submitted_at)

    def test_status_history_records_transition(self):
        application = Application.objects.create(student=self.student, scholarship=self.scholarship)
        ApplicationStatusHistory.objects.create(
            application=application, from_status=ApplicationStatus.DRAFT,
            to_status=ApplicationStatus.SUBMITTED,
        )
        self.assertEqual(application.status_history.count(), 1)
        self.assertEqual(application.status_history.first().to_status, ApplicationStatus.SUBMITTED)


class BookmarkModelTests(TestCase):
    def test_bookmark_uniqueness_per_student_scholarship(self):
        student_user = User.objects.create_user(
            username="applicant2", password="a-strong-pass-123", role=RoleChoices.STUDENT
        )
        student = StudentProfile.objects.create(
            user=student_user, university="IUBAT", department="CSE",
            cgpa=Decimal("3.50"), academic_level="Undergraduate",
            family_monthly_income=Decimal("20000.00"),
        )
        provider_user = User.objects.create_user(
            username="prov5", password="a-strong-pass-123", role=RoleChoices.PROVIDER
        )
        provider = ProviderProfile.objects.create(
            user=provider_user, organization_name="Foundation E", contact_email="e@example.com",
        )
        scholarship = Scholarship.objects.create(
            provider=provider, title="Scholarship F", description="desc",
            amount=Decimal("20000.00"), deadline=timezone.now() + timezone.timedelta(days=8),
        )
        Bookmark.objects.create(student=student, scholarship=scholarship)
        from django.db.utils import IntegrityError

        with self.assertRaises(IntegrityError):
            Bookmark.objects.create(student=student, scholarship=scholarship)


# ===========================================================================
# PROMPT 6 — Application submission workflow (FR-09), provider review
# (FR-16), bookmarks (FR-13), and integration with Prompts 3-5.
# ===========================================================================

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.common.enums import ProviderVerificationStatus
from apps.scholarships.models import ScholarshipCriterion, ScholarshipCriterionWeight

from .services import ApplicationService


def _future_deadline(days=30):
    return timezone.now() + timezone.timedelta(days=days)


def _make_verified_provider(username):
    user = User.objects.create_user(username=username, password="a-strong-pass-123", role=RoleChoices.PROVIDER)
    profile = ProviderProfile.objects.create(
        user=user, organization_name=f"{username} Org", contact_email=f"{username}@example.com",
        verification_status=ProviderVerificationStatus.APPROVED,
    )
    return user, profile


def _make_student(username, cgpa="3.50", income="20000.00"):
    user = User.objects.create_user(username=username, password="a-strong-pass-123", role=RoleChoices.STUDENT)
    profile = StudentProfile.objects.create(
        user=user, university="IUBAT", department="CSE",
        cgpa=Decimal(cgpa), academic_level="Undergraduate",
        family_monthly_income=Decimal(income),
    )
    return user, profile


def _published_scholarship_with_criterion(provider, title, cgpa_required="3.00", mandatory=True, weight=100):
    scholarship = Scholarship.objects.create(
        provider=provider, title=title, description="desc",
        amount=Decimal("10000.00"), deadline=_future_deadline(),
        is_published=True, is_active=True,
    )
    criterion = ScholarshipCriterion.objects.create(
        scholarship=scholarship, criterion_type=ScholarshipCriterion.CriterionType.CGPA,
        comparison=ScholarshipCriterion.Comparison.GTE, required_value=cgpa_required, is_mandatory=mandatory,
    )
    ScholarshipCriterionWeight.objects.create(criterion=criterion, weight_percent=Decimal(str(weight)))
    return scholarship


# ---------------------------------------------------------------------
# CREATE DRAFT / REAPPLICATION POLICY
# ---------------------------------------------------------------------

class CreateDraftTests(TestCase):
    def setUp(self):
        _, self.provider = _make_verified_provider("draftprov")
        _, self.student = _make_student("draftstud", cgpa="3.80")
        self.scholarship = _published_scholarship_with_criterion(self.provider, "Draft Test")

    def test_create_draft_succeeds(self):
        application = ApplicationService.create_draft(student=self.student, scholarship=self.scholarship)
        self.assertEqual(application.status, ApplicationStatus.DRAFT)

    def test_cannot_create_second_open_draft_for_same_scholarship(self):
        ApplicationService.create_draft(student=self.student, scholarship=self.scholarship)
        with self.assertRaises(ValidationError):
            ApplicationService.create_draft(student=self.student, scholarship=self.scholarship)

    def test_can_reapply_after_rejection(self):
        """Explicit reapplication policy (Prompt 6): a REJECTED
        application does not block a new one."""
        first = ApplicationService.create_draft(student=self.student, scholarship=self.scholarship)
        first.status = ApplicationStatus.REJECTED
        first.save()
        second = ApplicationService.create_draft(student=self.student, scholarship=self.scholarship)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(Application.objects.filter(student=self.student, scholarship=self.scholarship).count(), 2)

    def test_rejected_application_history_is_never_mutated_by_reapplication(self):
        first = ApplicationService.create_draft(student=self.student, scholarship=self.scholarship)
        first.status = ApplicationStatus.REJECTED
        first.save()
        ApplicationStatusHistory.objects.create(
            application=first, from_status=ApplicationStatus.SHORTLISTED, to_status=ApplicationStatus.REJECTED,
        )
        ApplicationService.create_draft(student=self.student, scholarship=self.scholarship)
        first.refresh_from_db()
        self.assertEqual(first.status, ApplicationStatus.REJECTED)
        self.assertEqual(first.status_history.count(), 1)

    def test_cannot_create_draft_for_unpublished_scholarship(self):
        unpublished = Scholarship.objects.create(
            provider=self.provider, title="Unpublished", description="desc",
            amount=Decimal("5000.00"), deadline=_future_deadline(), is_published=False,
        )
        with self.assertRaises(ValidationError):
            ApplicationService.create_draft(student=self.student, scholarship=unpublished)


# ---------------------------------------------------------------------
# SUBMISSION / ELIGIBILITY GATE
# ---------------------------------------------------------------------

class SubmissionEligibilityGateTests(TestCase):
    def setUp(self):
        _, self.provider = _make_verified_provider("submitprov")

    def test_submit_succeeds_when_eligible(self):
        _, student = _make_student("submiteligible", cgpa="3.80")
        scholarship = _published_scholarship_with_criterion(self.provider, "Submit Eligible", cgpa_required="3.50")
        application = ApplicationService.create_draft(student=student, scholarship=scholarship)
        result = ApplicationService.submit(application, actor=student.user)
        self.assertEqual(result.status, ApplicationStatus.SUBMITTED)
        self.assertIsNotNone(result.submitted_at)

    def test_submit_blocked_when_not_eligible_mandatory_criterion_fails(self):
        _, student = _make_student("submitnoteligible", cgpa="2.00")
        scholarship = _published_scholarship_with_criterion(self.provider, "Submit Not Eligible", cgpa_required="3.50", mandatory=True)
        application = ApplicationService.create_draft(student=student, scholarship=scholarship)
        with self.assertRaises(ValidationError):
            ApplicationService.submit(application, actor=student.user)
        application.refresh_from_db()
        self.assertEqual(application.status, ApplicationStatus.DRAFT)  # unchanged

    def test_submit_allowed_with_warning_when_missing_information(self):
        """Missing Information does not hard-block submission (distinct
        from Not Eligible) -- FR-07's own three-way distinction."""
        _, student = _make_student("submitmissing")
        scholarship = Scholarship.objects.create(
            provider=self.provider, title="Submit Missing Info", description="desc",
            amount=Decimal("5000.00"), deadline=_future_deadline(), is_published=True, is_active=True,
        )
        criterion = ScholarshipCriterion.objects.create(
            scholarship=scholarship, criterion_type=ScholarshipCriterion.CriterionType.LOCATION,
            comparison=ScholarshipCriterion.Comparison.EQUALS, required_value="Dhaka",
        )
        ScholarshipCriterionWeight.objects.create(criterion=criterion, weight_percent=Decimal("100"))
        application = ApplicationService.create_draft(student=student, scholarship=scholarship)
        result = ApplicationService.submit(application, actor=student.user)
        self.assertEqual(result.status, ApplicationStatus.SUBMITTED)

    def test_cannot_submit_already_submitted_application(self):
        _, student = _make_student("submittwice", cgpa="3.80")
        scholarship = _published_scholarship_with_criterion(self.provider, "Submit Twice", cgpa_required="3.50")
        application = ApplicationService.create_draft(student=student, scholarship=scholarship)
        ApplicationService.submit(application, actor=student.user)
        with self.assertRaises(ValidationError):
            ApplicationService.submit(application, actor=student.user)

    def test_submit_creates_status_history_row(self):
        _, student = _make_student("submithistory", cgpa="3.80")
        scholarship = _published_scholarship_with_criterion(self.provider, "Submit History", cgpa_required="3.50")
        application = ApplicationService.create_draft(student=student, scholarship=scholarship)
        ApplicationService.submit(application, actor=student.user)
        self.assertEqual(application.status_history.count(), 1)
        self.assertEqual(application.status_history.first().to_status, ApplicationStatus.SUBMITTED)

    def test_submit_creates_audit_log_entry(self):
        _, student = _make_student("submitaudit", cgpa="3.80")
        scholarship = _published_scholarship_with_criterion(self.provider, "Submit Audit", cgpa_required="3.50")
        application = ApplicationService.create_draft(student=student, scholarship=scholarship)
        ApplicationService.submit(application, actor=student.user)
        self.assertTrue(
            AuditLog.objects.filter(event_type=AuditLog.EventType.APPLICATION_STATUS_CHANGED).exists()
        )

    def test_readiness_never_blocks_submission(self):
        """Readiness (FR-08) is informational only at submit time --
        required documents (always incomplete, Prompt 5) never block
        submission on their own."""
        _, student = _make_student("submitreadiness", cgpa="3.80")
        scholarship = _published_scholarship_with_criterion(self.provider, "Submit Readiness", cgpa_required="3.50")
        scholarship.required_documents = ["Recommendation Letter"]
        scholarship.save()
        application = ApplicationService.create_draft(student=student, scholarship=scholarship)
        result = ApplicationService.submit(application, actor=student.user)
        self.assertEqual(result.status, ApplicationStatus.SUBMITTED)


# ---------------------------------------------------------------------
# PROVIDER-DRIVEN STATUS TRANSITIONS (FR-16)
# ---------------------------------------------------------------------

class StatusTransitionTests(TestCase):
    def setUp(self):
        _, self.provider = _make_verified_provider("transprov")
        _, self.student = _make_student("transstud", cgpa="3.80")
        self.scholarship = _published_scholarship_with_criterion(self.provider, "Trans Test", cgpa_required="3.50")
        self.application = ApplicationService.create_draft(student=self.student, scholarship=self.scholarship)
        ApplicationService.submit(self.application, actor=self.student.user)

    def test_valid_transition_submitted_to_under_review(self):
        ApplicationService.transition_status(self.application, ApplicationStatus.UNDER_REVIEW, actor=self.provider.user)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, ApplicationStatus.UNDER_REVIEW)

    def test_full_lifecycle_to_approved(self):
        ApplicationService.transition_status(self.application, ApplicationStatus.UNDER_REVIEW, actor=self.provider.user)
        ApplicationService.transition_status(self.application, ApplicationStatus.SHORTLISTED, actor=self.provider.user)
        ApplicationService.transition_status(self.application, ApplicationStatus.APPROVED, actor=self.provider.user)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, ApplicationStatus.APPROVED)
        self.assertEqual(self.application.status_history.count(), 4)  # submit + 3 transitions

    def test_cannot_skip_under_review_straight_to_approved(self):
        with self.assertRaises(ValidationError):
            ApplicationService.transition_status(self.application, ApplicationStatus.APPROVED, actor=self.provider.user)

    def test_cannot_transition_from_terminal_status(self):
        ApplicationService.transition_status(self.application, ApplicationStatus.UNDER_REVIEW, actor=self.provider.user)
        ApplicationService.transition_status(self.application, ApplicationStatus.REJECTED, actor=self.provider.user)
        with self.assertRaises(ValidationError):
            ApplicationService.transition_status(self.application, ApplicationStatus.UNDER_REVIEW, actor=self.provider.user)

    def test_reject_from_under_review_is_allowed(self):
        ApplicationService.transition_status(self.application, ApplicationStatus.UNDER_REVIEW, actor=self.provider.user)
        ApplicationService.transition_status(self.application, ApplicationStatus.REJECTED, actor=self.provider.user)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, ApplicationStatus.REJECTED)

    def test_each_transition_creates_history_and_audit_row(self):
        before_history = self.application.status_history.count()
        before_audit = AuditLog.objects.filter(event_type=AuditLog.EventType.APPLICATION_STATUS_CHANGED).count()
        ApplicationService.transition_status(self.application, ApplicationStatus.UNDER_REVIEW, actor=self.provider.user, note="Looks good")
        self.assertEqual(self.application.status_history.count(), before_history + 1)
        self.assertEqual(
            AuditLog.objects.filter(event_type=AuditLog.EventType.APPLICATION_STATUS_CHANGED).count(),
            before_audit + 1,
        )


# ---------------------------------------------------------------------
# OWNERSHIP & SECURITY (student cannot access another's application;
# provider cannot access another provider's applications)
# ---------------------------------------------------------------------

class ApplicationOwnershipTests(TestCase):
    def setUp(self):
        _, self.provider_a = _make_verified_provider("ownprovA")
        _, self.provider_b = _make_verified_provider("ownprovB")
        _, self.student_a = _make_student("ownstudA", cgpa="3.80")
        _, self.student_b = _make_student("ownstudB", cgpa="3.80")
        self.scholarship_a = _published_scholarship_with_criterion(self.provider_a, "Own Test A", cgpa_required="3.50")
        self.application_a = ApplicationService.create_draft(student=self.student_a, scholarship=self.scholarship_a)

    def test_student_can_fetch_own_application(self):
        result = ApplicationService.get_owned_application_or_403(self.application_a.pk, self.student_a)
        self.assertEqual(result, self.application_a)

    def test_student_b_cannot_fetch_student_as_application(self):
        with self.assertRaises(PermissionDenied):
            ApplicationService.get_owned_application_or_403(self.application_a.pk, self.student_b)

    def test_owning_provider_can_fetch_application(self):
        result = ApplicationService.get_provider_managed_application_or_403(self.application_a.pk, self.provider_a)
        self.assertEqual(result, self.application_a)

    def test_other_provider_cannot_fetch_application_for_scholarship_they_dont_own(self):
        with self.assertRaises(PermissionDenied):
            ApplicationService.get_provider_managed_application_or_403(self.application_a.pk, self.provider_b)


class ApplicationViewSecurityTests(TestCase):
    def setUp(self):
        self.client = Client()
        _, self.provider_a = _make_verified_provider("viewprovA")
        _, self.provider_b = _make_verified_provider("viewprovB")
        _, self.student_a = _make_student("viewstudA", cgpa="3.80")
        _, self.student_b = _make_student("viewstudB", cgpa="3.80")
        self.scholarship = _published_scholarship_with_criterion(self.provider_a, "View Sec Test", cgpa_required="3.50")
        self.application = ApplicationService.create_draft(student=self.student_a, scholarship=self.scholarship)

    def test_owner_can_view_own_application_detail(self):
        self.client.login(username="viewstudA", password="a-strong-pass-123")
        response = self.client.get(reverse("applications:detail", args=[self.application.pk]))
        self.assertEqual(response.status_code, 200)

    def test_other_student_gets_403_on_application_detail(self):
        self.client.login(username="viewstudB", password="a-strong-pass-123")
        response = self.client.get(reverse("applications:detail", args=[self.application.pk]))
        self.assertEqual(response.status_code, 403)

    def test_other_student_cannot_submit_someone_elses_application(self):
        self.client.login(username="viewstudB", password="a-strong-pass-123")
        response = self.client.post(reverse("applications:submit", args=[self.application.pk]))
        self.assertEqual(response.status_code, 403)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, ApplicationStatus.DRAFT)

    def test_owning_provider_can_view_application_for_review(self):
        self.client.login(username="viewprovA", password="a-strong-pass-123")
        response = self.client.get(reverse("applications:provider_detail", args=[self.application.pk]))
        self.assertEqual(response.status_code, 200)

    def test_other_provider_gets_403_on_provider_review_view(self):
        self.client.login(username="viewprovB", password="a-strong-pass-123")
        response = self.client.get(reverse("applications:provider_detail", args=[self.application.pk]))
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_access_provider_review_routes(self):
        self.client.login(username="viewstudA", password="a-strong-pass-123")
        response = self.client.get(reverse("applications:provider_list"))
        self.assertEqual(response.status_code, 403)

    def test_provider_cannot_access_student_application_list(self):
        self.client.login(username="viewprovA", password="a-strong-pass-123")
        response = self.client.get(reverse("applications:my_applications"))
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_user_redirected_from_application_detail(self):
        response = self.client.get(reverse("applications:detail", args=[self.application.pk]))
        self.assertEqual(response.status_code, 302)

    def test_two_students_applications_never_cross_contaminate(self):
        scholarship_b = _published_scholarship_with_criterion(self.provider_a, "Cross Test", cgpa_required="3.50")
        application_b = ApplicationService.create_draft(student=self.student_b, scholarship=scholarship_b)

        self.client.login(username="viewstudA", password="a-strong-pass-123")
        response = self.client.get(reverse("applications:my_applications"))
        self.assertNotContains(response, "Cross Test")


# ---------------------------------------------------------------------
# BOOKMARKS (FR-13)
# ---------------------------------------------------------------------

class BookmarkViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        _, self.provider = _make_verified_provider("bmprov")
        self.scholarship = _published_scholarship_with_criterion(self.provider, "Bookmark Test")
        _, self.student = _make_student("bmstud")

    def test_toggle_bookmark_creates_then_removes(self):
        self.client.login(username="bmstud", password="a-strong-pass-123")
        self.client.post(reverse("applications:toggle_bookmark", args=[self.scholarship.pk]))
        self.assertTrue(Bookmark.objects.filter(student=self.student, scholarship=self.scholarship).exists())

        self.client.post(reverse("applications:toggle_bookmark", args=[self.scholarship.pk]))
        self.assertFalse(Bookmark.objects.filter(student=self.student, scholarship=self.scholarship).exists())

    def test_my_bookmarks_view_shows_only_own_bookmarks(self):
        _, other_student = _make_student("bmotherstud")
        Bookmark.objects.create(student=self.student, scholarship=self.scholarship)
        Bookmark.objects.create(student=other_student, scholarship=self.scholarship)

        self.client.login(username="bmstud", password="a-strong-pass-123")
        response = self.client.get(reverse("applications:my_bookmarks"))
        self.assertEqual(len(response.context["bookmarks"]), 1)

    def test_provider_cannot_bookmark(self):
        self.client.login(username="bmprov", password="a-strong-pass-123")
        response = self.client.post(reverse("applications:toggle_bookmark", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------
# END-TO-END VIEW WORKFLOW
# ---------------------------------------------------------------------

class EndToEndApplicationWorkflowTests(TestCase):
    def setUp(self):
        self.client = Client()
        _, self.provider = _make_verified_provider("e2eprov")
        _, self.student = _make_student("e2estud", cgpa="3.80")
        self.scholarship = _published_scholarship_with_criterion(self.provider, "E2E Test", cgpa_required="3.50")

    def test_full_apply_and_submit_flow_via_views(self):
        self.client.login(username="e2estud", password="a-strong-pass-123")

        # Apply (creates draft)
        response = self.client.post(reverse("applications:apply", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 302)
        application = Application.objects.get(student=self.student, scholarship=self.scholarship)
        self.assertEqual(application.status, ApplicationStatus.DRAFT)

        # Submit
        response = self.client.post(reverse("applications:submit", args=[application.pk]))
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.status, ApplicationStatus.SUBMITTED)

    def test_apply_confirm_page_shows_eligibility_and_readiness(self):
        self.client.login(username="e2estud", password="a-strong-pass-123")
        response = self.client.get(reverse("applications:apply", args=[self.scholarship.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("overall_status", response.context)
        self.assertIn("readiness", response.context)

    def test_cannot_apply_to_unpublished_scholarship_via_view(self):
        unpublished = Scholarship.objects.create(
            provider=self.provider, title="E2E Unpublished", description="desc",
            amount=Decimal("1000.00"), deadline=_future_deadline(), is_published=False,
        )
        self.client.login(username="e2estud", password="a-strong-pass-123")
        response = self.client.get(reverse("applications:apply", args=[unpublished.pk]))
        self.assertEqual(response.status_code, 404)

    def test_full_provider_review_flow_via_views(self):
        application = ApplicationService.create_draft(student=self.student, scholarship=self.scholarship)
        ApplicationService.submit(application, actor=self.student.user)

        self.client.login(username="e2eprov", password="a-strong-pass-123")
        response = self.client.post(
            reverse("applications:provider_detail", args=[application.pk]),
            {"to_status": ApplicationStatus.UNDER_REVIEW, "note": "Reviewing now"},
        )
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.status, ApplicationStatus.UNDER_REVIEW)
