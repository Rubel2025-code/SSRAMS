"""
apps/accounts/tests.py

Foundation test coverage (project brief Step 14): user model, role
system, core model relationships, and basic authentication behavior.
Recommendation/eligibility/AI logic is explicitly out of scope for this
prompt's tests — those land in later prompts alongside the features.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.common.enums import ProviderVerificationStatus, RoleChoices

from .models import ProviderProfile, ProviderVerification, StudentProfile, User


class UserModelTests(TestCase):
    def test_create_student_user_has_student_role_and_flags(self):
        user = User.objects.create_user(
            username="alice", password="a-strong-pass-123", role=RoleChoices.STUDENT
        )
        self.assertEqual(user.role, RoleChoices.STUDENT)
        self.assertTrue(user.is_student)
        self.assertFalse(user.is_provider)
        self.assertFalse(user.is_admin_role)
        self.assertTrue(user.is_account_active)

    def test_create_provider_user_has_provider_role(self):
        user = User.objects.create_user(
            username="acme", password="a-strong-pass-123", role=RoleChoices.PROVIDER
        )
        self.assertTrue(user.is_provider)
        self.assertFalse(user.is_student)

    def test_str_includes_role_display(self):
        user = User.objects.create_user(
            username="bob", password="a-strong-pass-123", role=RoleChoices.STUDENT,
            first_name="Bob", last_name="Rahman",
        )
        self.assertIn("Student", str(user))


class StudentProfileRelationshipTests(TestCase):
    def test_one_to_one_relationship_and_reverse_accessor(self):
        user = User.objects.create_user(
            username="carol", password="a-strong-pass-123", role=RoleChoices.STUDENT
        )
        profile = StudentProfile.objects.create(
            user=user,
            university="IUBAT",
            department="CSE",
            cgpa=Decimal("3.72"),
            academic_level="Undergraduate — 4th Year",
            family_monthly_income=Decimal("25000.00"),
        )
        self.assertEqual(user.student_profile, profile)
        self.assertEqual(profile.skills, [])

    def test_cgpa_precision(self):
        user = User.objects.create_user(
            username="dave", password="a-strong-pass-123", role=RoleChoices.STUDENT
        )
        profile = StudentProfile.objects.create(
            user=user,
            university="IUBAT",
            department="CSE",
            cgpa=Decimal("3.72"),
            academic_level="Undergraduate",
            family_monthly_income=Decimal("30000.00"),
        )
        profile.refresh_from_db()
        self.assertEqual(profile.cgpa, Decimal("3.72"))


class ProviderProfileAndVerificationTests(TestCase):
    def test_provider_profile_defaults_to_pending(self):
        user = User.objects.create_user(
            username="scholarco", password="a-strong-pass-123", role=RoleChoices.PROVIDER
        )
        profile = ProviderProfile.objects.create(
            user=user, organization_name="Scholar Co", contact_email="ops@scholarco.example",
        )
        self.assertEqual(profile.verification_status, ProviderVerificationStatus.PENDING)

    def test_verification_history_can_have_multiple_rows(self):
        user = User.objects.create_user(
            username="scholarco2", password="a-strong-pass-123", role=RoleChoices.PROVIDER
        )
        profile = ProviderProfile.objects.create(
            user=user, organization_name="Scholar Co 2", contact_email="ops2@scholarco.example",
        )
        ProviderVerification.objects.create(provider_profile=profile, status=ProviderVerificationStatus.REJECTED)
        ProviderVerification.objects.create(provider_profile=profile, status=ProviderVerificationStatus.PENDING)
        self.assertEqual(profile.verification_history.count(), 2)


class AuthenticationBehaviorTests(TestCase):
    def test_login_page_loads(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)

    def test_login_with_valid_credentials_redirects(self):
        User.objects.create_user(
            username="erin", password="a-strong-pass-123", role=RoleChoices.STUDENT
        )
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "erin", "password": "a-strong-pass-123"},
        )
        self.assertEqual(response.status_code, 302)

    def test_login_with_invalid_credentials_does_not_redirect(self):
        User.objects.create_user(
            username="frank", password="a-strong-pass-123", role=RoleChoices.STUDENT
        )
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "frank", "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 200)  # re-renders form with error

    def test_logout_redirects_to_login(self):
        User.objects.create_user(
            username="gina", password="a-strong-pass-123", role=RoleChoices.STUDENT
        )
        self.client.login(username="gina", password="a-strong-pass-123")
        response = self.client.post(reverse("accounts:logout"))
        self.assertEqual(response.status_code, 302)

    def test_logout_rejects_get(self):
        """Logout destroys the session and writes an audit row, so it must
        not be reachable by a GET (which any third-party page can trigger)."""
        User.objects.create_user(
            username="gina_get", password="a-strong-pass-123", role=RoleChoices.STUDENT
        )
        self.client.login(username="gina_get", password="a-strong-pass-123")
        response = self.client.get(reverse("accounts:logout"))
        self.assertEqual(response.status_code, 405)
        # ...and the session must survive the rejected request.
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_student_registration_creates_account_with_correct_role(self):
        response = self.client.post(
            reverse("accounts:register_student"),
            {
                "username": "newstudent",
                "email": "newstudent@example.com",
                "first_name": "New",
                "last_name": "Student",
                "university": "IUBAT",
                "department": "CSE",
                "cgpa": "3.50",
                "academic_level": "Undergraduate",
                "family_monthly_income": "20000.00",
                "password1": "Sup3r-Str0ng-Pass!",
                "password2": "Sup3r-Str0ng-Pass!",
            },
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="newstudent")
        self.assertEqual(user.role, RoleChoices.STUDENT)

    def test_provider_registration_creates_account_and_profile(self):
        response = self.client.post(
            reverse("accounts:register_provider"),
            {
                "username": "newprovider",
                "email": "newprovider@example.com",
                "first_name": "New",
                "last_name": "Provider",
                "organization_name": "New Provider Org",
                "password1": "Sup3r-Str0ng-Pass!",
                "password2": "Sup3r-Str0ng-Pass!",
            },
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="newprovider")
        self.assertEqual(user.role, RoleChoices.PROVIDER)
        self.assertEqual(user.provider_profile.organization_name, "New Provider Org")


# ===========================================================================
# PROMPT 2 — profile creation-at-registration, profile view/edit, RBAC
# cross-role enforcement, ownership protection, verification workflow,
# and audit logging.
# ===========================================================================

from apps.audit.models import AuditLog
from apps.audit.services import log_event


class RegistrationCreatesProfileTests(TestCase):
    """FR-02/FR-03: registration must create the full profile, not just the account."""

    def test_student_registration_creates_student_profile_with_submitted_data(self):
        self.client.post(
            reverse("accounts:register_student"),
            {
                "username": "profstud", "email": "profstud@example.com",
                "first_name": "Prof", "last_name": "Stud",
                "university": "IUBAT", "department": "CSE",
                "cgpa": "3.85", "academic_level": "Undergraduate — 4th Year",
                "family_monthly_income": "22000.00", "location": "Dhaka",
                "password1": "Sup3r-Str0ng-Pass!", "password2": "Sup3r-Str0ng-Pass!",
            },
        )
        user = User.objects.get(username="profstud")
        profile = user.student_profile
        self.assertEqual(profile.university, "IUBAT")
        self.assertEqual(profile.cgpa, Decimal("3.85"))
        self.assertEqual(profile.location, "Dhaka")

    def test_provider_registration_creates_pending_verification_row(self):
        self.client.post(
            reverse("accounts:register_provider"),
            {
                "username": "profprov", "email": "profprov@example.com",
                "first_name": "Prof", "last_name": "Prov",
                "organization_name": "Prof Provider Org",
                "submitted_documents_note": "Registration certificate attached.",
                "password1": "Sup3r-Str0ng-Pass!", "password2": "Sup3r-Str0ng-Pass!",
            },
        )
        user = User.objects.get(username="profprov")
        profile = user.provider_profile
        self.assertEqual(profile.verification_status, ProviderVerificationStatus.PENDING)
        self.assertEqual(profile.verification_history.count(), 1)
        submission = profile.verification_history.first()
        self.assertEqual(submission.status, ProviderVerificationStatus.PENDING)
        self.assertEqual(submission.submitted_documents_note, "Registration certificate attached.")

    def test_registration_missing_required_profile_field_is_rejected(self):
        """Server-side validation: missing CGPA must not silently create a broken profile."""
        response = self.client.post(
            reverse("accounts:register_student"),
            {
                "username": "badstud", "email": "badstud@example.com",
                "first_name": "Bad", "last_name": "Stud",
                "university": "IUBAT", "department": "CSE",
                # cgpa omitted
                "academic_level": "Undergraduate",
                "family_monthly_income": "20000.00",
                "password1": "Sup3r-Str0ng-Pass!", "password2": "Sup3r-Str0ng-Pass!",
            },
        )
        self.assertEqual(response.status_code, 200)  # re-renders with errors
        self.assertFalse(User.objects.filter(username="badstud").exists())

    def test_registration_invalid_cgpa_is_rejected(self):
        response = self.client.post(
            reverse("accounts:register_student"),
            {
                "username": "badcgpa", "email": "badcgpa@example.com",
                "first_name": "Bad", "last_name": "Cgpa",
                "university": "IUBAT", "department": "CSE",
                "cgpa": "9.99",  # out of 0-4 range
                "academic_level": "Undergraduate",
                "family_monthly_income": "20000.00",
                "password1": "Sup3r-Str0ng-Pass!", "password2": "Sup3r-Str0ng-Pass!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="badcgpa").exists())

    def test_duplicate_username_is_rejected(self):
        User.objects.create_user(username="dupeuser", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        response = self.client.post(
            reverse("accounts:register_student"),
            {
                "username": "dupeuser", "email": "dupe2@example.com",
                "first_name": "Dupe", "last_name": "User",
                "university": "IUBAT", "department": "CSE",
                "cgpa": "3.00", "academic_level": "Undergraduate",
                "family_monthly_income": "20000.00",
                "password1": "Sup3r-Str0ng-Pass!", "password2": "Sup3r-Str0ng-Pass!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username="dupeuser").count(), 1)

    def test_cannot_register_as_administrator(self):
        """No registration form exposes role=admin at all — confirmed by URL absence."""
        from django.urls import NoReverseMatch

        with self.assertRaises(NoReverseMatch):
            reverse("accounts:register_admin")


class StudentProfileViewEditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="viewstud", password="a-strong-pass-123", role=RoleChoices.STUDENT
        )
        self.profile = StudentProfile.objects.create(
            user=self.user, university="IUBAT", department="CSE",
            cgpa=Decimal("3.60"), academic_level="Undergraduate",
            family_monthly_income=Decimal("18000.00"),
        )

    def test_profile_view_requires_login(self):
        response = self.client.get(reverse("accounts:student_profile"))
        self.assertEqual(response.status_code, 302)

    def test_authenticated_student_can_view_own_profile(self):
        self.client.login(username="viewstud", password="a-strong-pass-123")
        response = self.client.get(reverse("accounts:student_profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IUBAT")

    def test_student_can_edit_own_profile(self):
        self.client.login(username="viewstud", password="a-strong-pass-123")
        response = self.client.post(
            reverse("accounts:student_profile_edit"),
            {
                "university": "IUBAT", "department": "EEE",
                "cgpa": "3.90", "academic_level": "Undergraduate — 4th Year",
                "family_monthly_income": "25000.00", "location": "Dhaka",
                "skills": "Python, Django", "interests": "AI",
                "achievements": "", "extracurriculars": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.department, "EEE")
        self.assertEqual(self.profile.cgpa, Decimal("3.90"))
        self.assertEqual(self.profile.skills, ["Python", "Django"])

    def test_edit_invalid_cgpa_is_rejected_and_not_saved(self):
        self.client.login(username="viewstud", password="a-strong-pass-123")
        response = self.client.post(
            reverse("accounts:student_profile_edit"),
            {
                "university": "IUBAT", "department": "CSE",
                "cgpa": "5.50",  # invalid
                "academic_level": "Undergraduate",
                "family_monthly_income": "18000.00",
                "skills": "", "interests": "", "achievements": "", "extracurriculars": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.cgpa, Decimal("3.60"))  # unchanged

    def test_negative_income_is_rejected(self):
        self.client.login(username="viewstud", password="a-strong-pass-123")
        response = self.client.post(
            reverse("accounts:student_profile_edit"),
            {
                "university": "IUBAT", "department": "CSE", "cgpa": "3.60",
                "academic_level": "Undergraduate",
                "family_monthly_income": "-500.00",
                "skills": "", "interests": "", "achievements": "", "extracurriculars": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.family_monthly_income, Decimal("18000.00"))


class ProviderProfileViewEditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="viewprov", password="a-strong-pass-123", role=RoleChoices.PROVIDER
        )
        self.profile = ProviderProfile.objects.create(
            user=self.user, organization_name="View Org", contact_email="view@example.com",
        )

    def test_authenticated_provider_can_view_own_profile(self):
        self.client.login(username="viewprov", password="a-strong-pass-123")
        response = self.client.get(reverse("accounts:provider_profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "View Org")

    def test_provider_can_edit_own_profile(self):
        self.client.login(username="viewprov", password="a-strong-pass-123")
        response = self.client.post(
            reverse("accounts:provider_profile_edit"),
            {
                "organization_name": "Renamed Org", "organization_type": "NGO",
                "contact_email": "new@example.com", "contact_phone": "",
                "website": "", "description": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.organization_name, "Renamed Org")

    def test_provider_cannot_change_own_verification_status_via_edit_form(self):
        """verification_status is not a field on ProviderProfileForm — attempting
        to inject it via POST must have no effect (FR-03 integrity)."""
        self.client.login(username="viewprov", password="a-strong-pass-123")
        self.client.post(
            reverse("accounts:provider_profile_edit"),
            {
                "organization_name": "View Org", "organization_type": "",
                "contact_email": "view@example.com", "contact_phone": "",
                "website": "", "description": "",
                "verification_status": "approved",  # attempted injection
            },
        )
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, ProviderVerificationStatus.PENDING)


class RBACCrossRoleAccessTests(TestCase):
    """A Student must never reach Provider/Admin views, and vice versa,
    purely by requesting the URL (FR-01/§3.3.3)."""

    def setUp(self):
        self.student = User.objects.create_user(
            username="rbacstud", password="a-strong-pass-123", role=RoleChoices.STUDENT
        )
        StudentProfile.objects.create(
            user=self.student, university="IUBAT", department="CSE",
            cgpa=Decimal("3.5"), academic_level="Undergraduate",
            family_monthly_income=Decimal("15000.00"),
        )
        self.provider_user = User.objects.create_user(
            username="rbacprov", password="a-strong-pass-123", role=RoleChoices.PROVIDER
        )
        ProviderProfile.objects.create(
            user=self.provider_user, organization_name="RBAC Org", contact_email="rbac@example.com",
        )
        self.admin_user = User.objects.create_user(
            username="rbacadmin", password="a-strong-pass-123", role=RoleChoices.ADMIN
        )

    def test_student_cannot_access_provider_profile_view(self):
        self.client.login(username="rbacstud", password="a-strong-pass-123")
        response = self.client.get(reverse("accounts:provider_profile"))
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_access_admin_verification_list(self):
        self.client.login(username="rbacstud", password="a-strong-pass-123")
        response = self.client.get(reverse("accounts:admin_verification_list"))
        self.assertEqual(response.status_code, 403)

    def test_provider_cannot_access_student_profile_view(self):
        self.client.login(username="rbacprov", password="a-strong-pass-123")
        response = self.client.get(reverse("accounts:student_profile"))
        self.assertEqual(response.status_code, 403)

    def test_provider_cannot_access_admin_verification_list(self):
        self.client.login(username="rbacprov", password="a-strong-pass-123")
        response = self.client.get(reverse("accounts:admin_verification_list"))
        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_access_student_profile_view(self):
        self.client.login(username="rbacadmin", password="a-strong-pass-123")
        response = self.client.get(reverse("accounts:student_profile"))
        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_access_provider_profile_view(self):
        self.client.login(username="rbacadmin", password="a-strong-pass-123")
        response = self.client.get(reverse("accounts:provider_profile"))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_verification_list(self):
        self.client.login(username="rbacadmin", password="a-strong-pass-123")
        response = self.client.get(reverse("accounts:admin_verification_list"))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_user_redirected_from_all_protected_views(self):
        for url_name in (
            "accounts:student_profile", "accounts:provider_profile",
            "accounts:admin_verification_list",
        ):
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 302, f"{url_name} should redirect anonymous users")


class OwnershipProtectionTests(TestCase):
    """
    Student A must never be able to view Student B's data, and likewise
    for providers. The student/provider profile views take NO id
    parameter and always operate on request.user's own profile — this
    test proves that by construction, logging in as A and requesting the
    profile view can only ever return A's own data, never B's, no matter
    what B's profile contains.
    """

    def test_student_a_sees_only_own_profile_data_never_student_bs(self):
        user_a = User.objects.create_user(username="studA", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        StudentProfile.objects.create(
            user=user_a, university="University A", department="CSE",
            cgpa=Decimal("3.10"), academic_level="Undergraduate",
            family_monthly_income=Decimal("10000.00"),
        )
        user_b = User.objects.create_user(username="studB", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        StudentProfile.objects.create(
            user=user_b, university="University B — Private", department="EEE",
            cgpa=Decimal("3.95"), academic_level="Graduate",
            family_monthly_income=Decimal("99999.00"),
        )

        self.client.login(username="studA", password="a-strong-pass-123")
        response = self.client.get(reverse("accounts:student_profile"))
        self.assertContains(response, "University A")
        self.assertNotContains(response, "University B — Private")

    def test_provider_a_sees_only_own_profile_data_never_provider_bs(self):
        user_a = User.objects.create_user(username="provA", password="a-strong-pass-123", role=RoleChoices.PROVIDER)
        ProviderProfile.objects.create(user=user_a, organization_name="Org A Confidential", contact_email="a@example.com")
        user_b = User.objects.create_user(username="provB", password="a-strong-pass-123", role=RoleChoices.PROVIDER)
        ProviderProfile.objects.create(user=user_b, organization_name="Org B Confidential", contact_email="b@example.com")

        self.client.login(username="provA", password="a-strong-pass-123")
        response = self.client.get(reverse("accounts:provider_profile"))
        self.assertContains(response, "Org A Confidential")
        self.assertNotContains(response, "Org B Confidential")

    def test_editing_own_profile_never_affects_another_students_profile(self):
        user_a = User.objects.create_user(username="editA", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        profile_a = StudentProfile.objects.create(
            user=user_a, university="A Uni", department="CSE",
            cgpa=Decimal("3.00"), academic_level="Undergraduate",
            family_monthly_income=Decimal("10000.00"),
        )
        user_b = User.objects.create_user(username="editB", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        profile_b = StudentProfile.objects.create(
            user=user_b, university="B Uni", department="EEE",
            cgpa=Decimal("3.20"), academic_level="Undergraduate",
            family_monthly_income=Decimal("12000.00"),
        )

        self.client.login(username="editA", password="a-strong-pass-123")
        self.client.post(
            reverse("accounts:student_profile_edit"),
            {
                "university": "A Uni Renamed", "department": "CSE",
                "cgpa": "3.00", "academic_level": "Undergraduate",
                "family_monthly_income": "10000.00",
                "skills": "", "interests": "", "achievements": "", "extracurriculars": "",
            },
        )
        profile_a.refresh_from_db()
        profile_b.refresh_from_db()
        self.assertEqual(profile_a.university, "A Uni Renamed")
        self.assertEqual(profile_b.university, "B Uni")  # untouched


class ProviderVerificationWorkflowTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="verifyadmin", password="a-strong-pass-123", role=RoleChoices.ADMIN
        )
        self.provider_user = User.objects.create_user(
            username="verifyprov", password="a-strong-pass-123", role=RoleChoices.PROVIDER
        )
        self.provider_profile = ProviderProfile.objects.create(
            user=self.provider_user, organization_name="Verify Org", contact_email="verify@example.com",
        )
        ProviderVerification.objects.create(
            provider_profile=self.provider_profile, status=ProviderVerificationStatus.PENDING,
        )

    def test_provider_starts_pending(self):
        self.assertEqual(self.provider_profile.verification_status, ProviderVerificationStatus.PENDING)

    def test_admin_can_view_pending_list(self):
        self.client.login(username="verifyadmin", password="a-strong-pass-123")
        response = self.client.get(reverse("accounts:admin_verification_list"))
        self.assertContains(response, "Verify Org")

    def test_admin_can_approve_provider(self):
        self.client.login(username="verifyadmin", password="a-strong-pass-123")
        response = self.client.post(
            reverse("accounts:admin_verification_detail", args=[self.provider_profile.pk]),
            {"decision": "approved", "decision_note": "Looks good."},
        )
        self.assertEqual(response.status_code, 302)
        self.provider_profile.refresh_from_db()
        self.assertEqual(self.provider_profile.verification_status, ProviderVerificationStatus.APPROVED)
        submission = self.provider_profile.verification_history.first()
        self.assertEqual(submission.status, ProviderVerificationStatus.APPROVED)
        self.assertEqual(submission.decided_by, self.admin_user)

    def test_admin_can_reject_provider(self):
        self.client.login(username="verifyadmin", password="a-strong-pass-123")
        response = self.client.post(
            reverse("accounts:admin_verification_detail", args=[self.provider_profile.pk]),
            {"decision": "rejected", "decision_note": "Insufficient evidence."},
        )
        self.assertEqual(response.status_code, 302)
        self.provider_profile.refresh_from_db()
        self.assertEqual(self.provider_profile.verification_status, ProviderVerificationStatus.REJECTED)

    def test_non_admin_cannot_decide_verification(self):
        self.client.login(username="verifyprov", password="a-strong-pass-123")
        response = self.client.post(
            reverse("accounts:admin_verification_detail", args=[self.provider_profile.pk]),
            {"decision": "approved", "decision_note": ""},
        )
        self.assertEqual(response.status_code, 403)
        self.provider_profile.refresh_from_db()
        self.assertEqual(self.provider_profile.verification_status, ProviderVerificationStatus.PENDING)

    def test_unverified_provider_cannot_perform_verified_only_action(self):
        """Exercises the existing apps.common.rbac.verified_provider_required
        decorator against a still-PENDING provider."""
        from django.http import HttpResponse
        from django.test import RequestFactory

        from apps.common.rbac import verified_provider_required

        @verified_provider_required
        def _restricted_view(request):
            return HttpResponse("should not reach here")

        factory = RequestFactory()
        request = factory.get("/fake-restricted/")
        request.user = self.provider_user
        # RequestFactory requests need a session/messages framework to run
        # through middleware that verified_provider_required relies on
        # (messages.warning) — attach minimal support.
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.middleware import SessionMiddleware

        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)

        response = _restricted_view(request)
        self.assertEqual(response.status_code, 302)  # redirected, not allowed through

    def test_verified_provider_passes_verified_only_check(self):
        self.provider_profile.verification_status = ProviderVerificationStatus.APPROVED
        self.provider_profile.save()

        from django.http import HttpResponse
        from django.test import RequestFactory

        from apps.common.rbac import verified_provider_required

        @verified_provider_required
        def _restricted_view(request):
            return HttpResponse("reached")

        factory = RequestFactory()
        request = factory.get("/fake-restricted/")
        request.user = self.provider_user

        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.middleware import SessionMiddleware

        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)

        response = _restricted_view(request)
        self.assertEqual(response.status_code, 200)


class AuditLoggingTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="auditadmin", password="a-strong-pass-123", role=RoleChoices.ADMIN
        )

    def test_student_registration_creates_audit_record(self):
        self.client.post(
            reverse("accounts:register_student"),
            {
                "username": "auditstud", "email": "auditstud@example.com",
                "first_name": "Audit", "last_name": "Stud",
                "university": "IUBAT", "department": "CSE",
                "cgpa": "3.40", "academic_level": "Undergraduate",
                "family_monthly_income": "15000.00",
                "password1": "Sup3r-Str0ng-Pass!", "password2": "Sup3r-Str0ng-Pass!",
            },
        )
        self.assertTrue(
            AuditLog.objects.filter(event_type=AuditLog.EventType.ACCOUNT_REGISTERED).exists()
        )

    def test_login_creates_audit_record(self):
        User.objects.create_user(username="auditlogin", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        self.client.post(reverse("accounts:login"), {"username": "auditlogin", "password": "a-strong-pass-123"})
        self.assertTrue(AuditLog.objects.filter(event_type=AuditLog.EventType.LOGIN).exists())

    def test_logout_creates_audit_record(self):
        User.objects.create_user(username="auditlogout", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        self.client.login(username="auditlogout", password="a-strong-pass-123")
        self.client.post(reverse("accounts:logout"))
        self.assertTrue(AuditLog.objects.filter(event_type=AuditLog.EventType.LOGOUT).exists())

    def test_profile_update_creates_audit_record(self):
        user = User.objects.create_user(username="auditedit", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        StudentProfile.objects.create(
            user=user, university="IUBAT", department="CSE",
            cgpa=Decimal("3.0"), academic_level="Undergraduate",
            family_monthly_income=Decimal("10000.00"),
        )
        self.client.login(username="auditedit", password="a-strong-pass-123")
        self.client.post(
            reverse("accounts:student_profile_edit"),
            {
                "university": "IUBAT", "department": "CSE", "cgpa": "3.10",
                "academic_level": "Undergraduate", "family_monthly_income": "10000.00",
                "skills": "", "interests": "", "achievements": "", "extracurriculars": "",
            },
        )
        self.assertTrue(AuditLog.objects.filter(event_type=AuditLog.EventType.PROFILE_UPDATED).exists())

    def test_provider_approval_creates_audit_record_with_actor(self):
        provider_user = User.objects.create_user(
            username="auditprov", password="a-strong-pass-123", role=RoleChoices.PROVIDER
        )
        provider_profile = ProviderProfile.objects.create(
            user=provider_user, organization_name="Audit Prov Org", contact_email="ap@example.com",
        )
        ProviderVerification.objects.create(provider_profile=provider_profile, status=ProviderVerificationStatus.PENDING)

        self.client.login(username="auditadmin", password="a-strong-pass-123")
        self.client.post(
            reverse("accounts:admin_verification_detail", args=[provider_profile.pk]),
            {"decision": "approved", "decision_note": ""},
        )
        entry = AuditLog.objects.filter(event_type=AuditLog.EventType.PROVIDER_APPROVED).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.admin_user)
        self.assertEqual(entry.related_object, provider_profile)

    def test_audit_log_never_stores_password(self):
        """Sanity check: no audit description accidentally contains a raw password."""
        self.client.post(
            reverse("accounts:register_student"),
            {
                "username": "auditnopass", "email": "auditnopass@example.com",
                "first_name": "No", "last_name": "Pass",
                "university": "IUBAT", "department": "CSE",
                "cgpa": "3.00", "academic_level": "Undergraduate",
                "family_monthly_income": "10000.00",
                "password1": "Sup3r-S3cr3t-Pass!", "password2": "Sup3r-S3cr3t-Pass!",
            },
        )
        for entry in AuditLog.objects.all():
            self.assertNotIn("Sup3r-S3cr3t-Pass!", entry.description)


class CSRFProtectionTests(TestCase):
    def test_login_post_without_csrf_token_is_rejected(self):
        csrf_client = self.client_class(enforce_csrf_checks=True)
        user = User.objects.create_user(username="csrfuser", password="a-strong-pass-123", role=RoleChoices.STUDENT)
        response = csrf_client.post(
            reverse("accounts:login"), {"username": "csrfuser", "password": "a-strong-pass-123"}
        )
        self.assertEqual(response.status_code, 403)
