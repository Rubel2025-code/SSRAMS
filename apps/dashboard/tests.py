"""
apps/dashboard/tests.py

Foundation tests: routing, login-required behavior, and role-based
template dispatch. No business-logic numbers are asserted since none
are computed yet (those land with the owning app's feature prompt).
"""

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.common.enums import RoleChoices


class DashboardRoutingTests(TestCase):
    def test_root_redirects_to_dashboard(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dashboard:home"), response.url)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_student_sees_student_template(self):
        User.objects.create_user(username="dashstud", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        self.client.login(username="dashstud", password="a-strong-pass-123")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/student_dashboard.html")

    def test_provider_sees_provider_template(self):
        User.objects.create_user(username="dashprov", password="a-strong-pass-123", role=RoleChoices.PROVIDER)
        self.client.login(username="dashprov", password="a-strong-pass-123")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/provider_dashboard.html")

    def test_admin_sees_admin_template(self):
        User.objects.create_user(username="dashadmin", password="a-strong-pass-123", role=RoleChoices.ADMIN)
        self.client.login(username="dashadmin", password="a-strong-pass-123")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/admin_dashboard.html")


class DashboardTopOpportunitiesPreviewTests(TestCase):
    """Prompt 5: student dashboard's Top Opportunities preview."""

    def test_student_with_no_profile_gets_empty_preview_not_a_crash(self):
        User.objects.create_user(username="dashnoprof", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        self.client.login(username="dashnoprof", password="a-strong-pass-123")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["top_opportunities_preview"], [])

    def test_student_with_profile_and_matching_scholarship_sees_preview_entry(self):
        from decimal import Decimal

        from apps.accounts.models import ProviderProfile, StudentProfile
        from apps.common.enums import ProviderVerificationStatus
        from apps.scholarships.models import Scholarship, ScholarshipCriterion, ScholarshipCriterionWeight
        from django.utils import timezone

        student_user = User.objects.create_user(username="dashwithprof", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        StudentProfile.objects.create(
            user=student_user, university="IUBAT", department="CSE",
            cgpa=Decimal("3.80"), academic_level="Undergraduate",
            family_monthly_income=Decimal("15000.00"),
        )
        provider_user = User.objects.create_user(username="dashprovwithsch", password="a-strong-pass-123", role=RoleChoices.PROVIDER)
        provider = ProviderProfile.objects.create(
            user=provider_user, organization_name="Dash Org", contact_email="dash@example.com",
            verification_status=ProviderVerificationStatus.APPROVED,
        )
        scholarship = Scholarship.objects.create(
            provider=provider, title="Dashboard Preview Scholarship", description="desc",
            amount=Decimal("5000.00"), deadline=timezone.now() + timezone.timedelta(days=10),
            is_published=True, is_active=True,
        )
        criterion = ScholarshipCriterion.objects.create(
            scholarship=scholarship, criterion_type=ScholarshipCriterion.CriterionType.CGPA,
            comparison=ScholarshipCriterion.Comparison.GTE, required_value="3.50",
        )
        ScholarshipCriterionWeight.objects.create(criterion=criterion, weight_percent=Decimal("100"))

        self.client.login(username="dashwithprof", password="a-strong-pass-123")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["top_opportunities_preview"]), 1)
        self.assertContains(response, "Dashboard Preview Scholarship")

    def test_preview_limited_to_three_entries(self):
        from decimal import Decimal

        from apps.accounts.models import ProviderProfile, StudentProfile
        from apps.common.enums import ProviderVerificationStatus
        from apps.scholarships.models import Scholarship, ScholarshipCriterion, ScholarshipCriterionWeight
        from django.utils import timezone

        student_user = User.objects.create_user(username="dashlimit", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        StudentProfile.objects.create(
            user=student_user, university="IUBAT", department="CSE",
            cgpa=Decimal("3.80"), academic_level="Undergraduate",
            family_monthly_income=Decimal("15000.00"),
        )
        provider_user = User.objects.create_user(username="dashlimitprov", password="a-strong-pass-123", role=RoleChoices.PROVIDER)
        provider = ProviderProfile.objects.create(
            user=provider_user, organization_name="Dash Limit Org", contact_email="dashlimit@example.com",
            verification_status=ProviderVerificationStatus.APPROVED,
        )
        for i in range(5):
            scholarship = Scholarship.objects.create(
                provider=provider, title=f"Dash Limit Scholarship {i}", description="desc",
                amount=Decimal("5000.00"), deadline=timezone.now() + timezone.timedelta(days=10),
                is_published=True, is_active=True,
            )
            criterion = ScholarshipCriterion.objects.create(
                scholarship=scholarship, criterion_type=ScholarshipCriterion.CriterionType.CGPA,
                comparison=ScholarshipCriterion.Comparison.GTE, required_value="3.50",
            )
            ScholarshipCriterionWeight.objects.create(criterion=criterion, weight_percent=Decimal("100"))

        self.client.login(username="dashlimit", password="a-strong-pass-123")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(len(response.context["top_opportunities_preview"]), 3)


class DashboardPendingApplicationCountTests(TestCase):
    """Prompt 6: provider dashboard's pending-application count."""

    def test_provider_with_no_scholarships_sees_zero(self):
        User.objects.create_user(username="dashnoprovsch", password="a-strong-pass-123", role=RoleChoices.PROVIDER)
        self.client.login(username="dashnoprovsch", password="a-strong-pass-123")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.context["pending_application_count"], 0)

    def test_provider_sees_correct_pending_count(self):
        from decimal import Decimal

        from apps.accounts.models import ProviderProfile, StudentProfile
        from apps.applications.models import Application
        from apps.applications.services import ApplicationService
        from apps.common.enums import ApplicationStatus, ProviderVerificationStatus
        from apps.scholarships.models import Scholarship, ScholarshipCriterion, ScholarshipCriterionWeight
        from django.utils import timezone

        provider_user = User.objects.create_user(username="dashpendprov", password="a-strong-pass-123", role=RoleChoices.PROVIDER)
        provider = ProviderProfile.objects.create(
            user=provider_user, organization_name="Dash Pending Org", contact_email="dp@example.com",
            verification_status=ProviderVerificationStatus.APPROVED,
        )
        scholarship = Scholarship.objects.create(
            provider=provider, title="Dash Pending Scholarship", description="desc",
            amount=Decimal("5000.00"), deadline=timezone.now() + timezone.timedelta(days=10),
            is_published=True, is_active=True,
        )
        criterion = ScholarshipCriterion.objects.create(
            scholarship=scholarship, criterion_type=ScholarshipCriterion.CriterionType.CGPA,
            comparison=ScholarshipCriterion.Comparison.GTE, required_value="3.00",
        )
        ScholarshipCriterionWeight.objects.create(criterion=criterion, weight_percent=Decimal("100"))

        student_user = User.objects.create_user(username="dashpendstud", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        student = StudentProfile.objects.create(
            user=student_user, university="IUBAT", department="CSE",
            cgpa=Decimal("3.80"), academic_level="Undergraduate",
            family_monthly_income=Decimal("15000.00"),
        )
        application = ApplicationService.create_draft(student=student, scholarship=scholarship)
        ApplicationService.submit(application, actor=student_user)  # -> SUBMITTED, should count

        draft_application = Application.objects.create(student=student, scholarship=scholarship)
        draft_application.status = ApplicationStatus.DRAFT
        draft_application.save()  # DRAFT should NOT count (student hasn't submitted)

        self.client.login(username="dashpendprov", password="a-strong-pass-123")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.context["pending_application_count"], 1)
