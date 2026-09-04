"""
apps/accounts/models.py

Owns: User, StudentProfile, ProviderProfile, ProviderVerification.

Design notes (see README.md "Database / Model Overview" for the full
relationship diagram):

- SRS §2.1 specifies three roles (Student, Scholarship Provider,
  Administrator) sharing ONE platform. The project brief (Step 5)
  explicitly asks for "a unified authentication architecture with
  role-based authorization" rather than separate auth systems per role.
  We implement this as a single custom ``User`` model with a ``role``
  field, plus a one-to-one "detail" profile per role that only applies
  to that role (StudentProfile / ProviderProfile). Administrators use
  the base User plus Django's built-in is_staff/is_superuser for the
  Django Admin foundation (Step 11) — SSRAMS's own Administrator
  dashboard (built in a later prompt) authorizes on ``role == ADMIN``.

- ProviderVerification is kept as its own model (not just a status field
  on ProviderProfile) because FR-03 / FR-18 require an auditable record
  of the verification *event* (who submitted what, when, and the
  admin decision) — a single mutable status field could not represent
  a resubmission-after-rejection history. ProviderProfile.verification_status
  is a denormalized "current state" field for fast, simple checks
  (e.g. in apps.common.rbac.verified_provider_required); the
  ProviderVerification rows are the source of truth for the history.
"""

from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.common.enums import ProviderVerificationStatus, RoleChoices
from apps.common.models import TimeStampedModel


class User(AbstractUser):
    """
    Custom user model (AUTH_USER_MODEL = "accounts.User").

    Extends Django's AbstractUser (keeps username/email/password/
    is_staff/is_superuser/is_active machinery) and adds the ``role``
    field that every other app's RBAC checks key off.
    """

    role = models.CharField(
        max_length=20,
        choices=RoleChoices.choices,
        help_text="The single role this account was registered as. "
        "SRS §2.1: Student, Scholarship Provider, or Administrator.",
    )

    # FR-01: "prevents unauthorized role escalation" — role is set once
    # at registration by the relevant app view and is not exposed on any
    # student/provider-facing "edit profile" form. Administrators change
    # it only through the Django Admin (is_staff-gated).
    is_account_active = models.BooleanField(
        default=True,
        help_text="Administrative kill-switch distinct from Django's own "
        "is_active, so audit.AuditLog can record *why* an admin suspended "
        "an account (FR-17) without touching Django's login machinery "
        "semantics directly.",
    )

    class Meta:
        indexes = [
            models.Index(fields=["role"]),
        ]

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"

    @property
    def is_student(self):
        return self.role == RoleChoices.STUDENT

    @property
    def is_provider(self):
        return self.role == RoleChoices.PROVIDER

    @property
    def is_admin_role(self):
        return self.role == RoleChoices.ADMIN


class StudentProfile(TimeStampedModel):
    """
    Student profile data (FR-02).

    This is the data that "feeds the recommendation engine, eligibility
    checker, readiness engine, Profile Improvement Advisor, and AI
    Advisor" per FR-02 — i.e. every downstream app in later prompts reads
    this model but never writes to it except through this app.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="student_profile",
        limit_choices_to={"role": RoleChoices.STUDENT},
    )

    university = models.CharField(max_length=255)
    department = models.CharField(max_length=255)
    cgpa = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        help_text="On a 4.00 scale, e.g. 3.72.",
    )
    academic_level = models.CharField(
        max_length=100,
        help_text='e.g. "Undergraduate — 3rd Year", "Graduate".',
    )
    family_monthly_income = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Monthly family income in BDT, used by income-based "
        "eligibility criteria (FR-07).",
    )
    location = models.CharField(max_length=255, blank=True)

    # FR-02 lists skills/interests/achievements/extracurriculars as
    # free-form profile inputs that feed later matching/gap logic. Stored
    # as JSON lists rather than a separate M2M "Skill" model for this
    # foundation prompt — Member 2/Member 4 may normalize this into a
    # dedicated Skill model in a later prompt if the matching logic needs
    # to query by individual skill; documented here so that decision is
    # visible rather than silent.
    skills = models.JSONField(default=list, blank=True)
    interests = models.JSONField(default=list, blank=True)
    achievements = models.JSONField(default=list, blank=True)
    extracurriculars = models.JSONField(default=list, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["department"]),
            models.Index(fields=["cgpa"]),
        ]

    def __str__(self):
        return f"StudentProfile<{self.user.username}>"


class ProviderProfile(TimeStampedModel):
    """
    Scholarship Provider organization profile (FR-03).

    ``verification_status`` is a denormalized read of the latest
    ProviderVerification decision, kept here so every other app's "can
    this provider publish?" check (FR-04) is a single indexed field
    lookup rather than a join + max(created_at) query. It is written
    only by ProviderVerification.save()/the admin verification workflow
    — never directly by provider-facing views.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="provider_profile",
        limit_choices_to={"role": RoleChoices.PROVIDER},
    )

    organization_name = models.CharField(max_length=255)
    organization_type = models.CharField(
        max_length=100,
        blank=True,
        help_text='e.g. "NGO", "Corporate Foundation", "Government Body".',
    )
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=30, blank=True)
    website = models.URLField(blank=True)
    description = models.TextField(blank=True)

    verification_status = models.CharField(
        max_length=20,
        choices=ProviderVerificationStatus.choices,
        default=ProviderVerificationStatus.PENDING,
        help_text="Denormalized current status — see ProviderVerification "
        "for the full decision history. Only admin-approved providers "
        "may publish scholarships (FR-03, FR-04).",
    )

    class Meta:
        indexes = [
            models.Index(fields=["verification_status"]),
        ]

    def __str__(self):
        return f"{self.organization_name} [{self.verification_status}]"


class ProviderVerification(TimeStampedModel):
    """
    One row per verification submission/decision for a provider (FR-03,
    FR-18). Kept separate from ProviderProfile so re-submission after a
    rejection is representable and auditable, rather than overwriting
    history.
    """

    provider_profile = models.ForeignKey(
        ProviderProfile,
        on_delete=models.CASCADE,
        related_name="verification_history",
    )
    submitted_documents_note = models.TextField(
        blank=True,
        help_text="Free-text description of verification evidence "
        "submitted; document upload UI is built in a later prompt.",
    )
    status = models.CharField(
        max_length=20,
        choices=ProviderVerificationStatus.choices,
        default=ProviderVerificationStatus.PENDING,
    )
    decided_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="provider_verifications_decided",
        limit_choices_to={"role": RoleChoices.ADMIN},
        help_text="The administrator who approved/rejected this submission.",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    class Meta:
        # "-id" is a tie-breaker, not a second sort concept: several rows
        # can share an identical created_at (auto_now_add resolution is
        # coarser than a request on some platforms — Windows clocks tick
        # at ~15ms), and "latest submission" is read with .first() in
        # apps.accounts.views.admin_verification_detail, so a tie must
        # not be resolved arbitrarily by the database.
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Verification<{self.provider_profile.organization_name}: {self.status}>"
