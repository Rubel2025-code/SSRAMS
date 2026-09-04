"""
apps/applications/services/__init__.py

Prompt 1 established this interface (ALLOWED_TRANSITIONS and both
method signatures below are UNCHANGED from that stub). Prompt 6 fully
implements it.

--------------------------------------------------------------------
OWNERSHIP -- the one choke point every application view uses
--------------------------------------------------------------------
Mirrors the exact pattern apps.scholarships.services.scholarship_service.
ScholarshipService.get_owned_scholarship_or_403 established in Prompt
3: ApplicationService.get_owned_application_or_403 (student's own
application) and get_provider_managed_application_or_403 (provider
reviewing an application to one of their own scholarships) are the
ONLY ways any view fetches an Application by id. No other lookup path
exists in this app's views.

--------------------------------------------------------------------
SUBMIT GATE -- what "must verify eligibility/readiness first" means
--------------------------------------------------------------------
FR-09's own module docstring (this file, unchanged from Prompt 1) says
submit() "must verify eligibility/readiness first via
EligibilityService/ReadinessService". This is deliberately NOT the
same bar as full ELIGIBLE status:

  - If overall eligibility (EligibilityService.rollup_status) is
    NOT_ELIGIBLE -- i.e. a MANDATORY criterion is genuinely unmet
    (EligibilityResult.is_blocking, Prompt 4) -- submission is
    BLOCKED. There is no SRS basis for letting a student submit an
    application the scholarship's own rules say they cannot satisfy.
  - If overall eligibility is MISSING_INFO, submission is ALLOWED but
    flagged with a warning message (the system lacking data about the
    student is not the same as the student failing a requirement --
    FR-07 itself treats these as distinct outcomes, and FR-20 already
    treats "near-eligible" scholarships as legitimate opportunities
    worth acting on, not hard-blocked ones).
  - Readiness (FR-08) is informational only at submit time -- a
    student may submit before every document is uploaded (there is no
    document-upload mechanism yet anyway, per Prompt 5's own
    documented limitation) -- ReadinessService is still called and its
    result stored in the AuditLog description for traceability, but it
    does not gate the transition.

This keeps the two SRS concepts (FR-07 eligibility, FR-08 readiness)
doing exactly what they each say and nothing more: eligibility can
block, readiness informs.

--------------------------------------------------------------------
REAPPLICATION POLICY -- explicit, not left ambiguous (SS/project brief)
--------------------------------------------------------------------
The SRS does not state whether a student may re-apply to the same
scholarship after a REJECTED decision (Prompt 1's model docstring for
Application flagged this exact gap and deferred it here). This service
resolves it explicitly: a student MAY create a new Application for a
scholarship they were previously REJECTED for (a new DRAFT row is
created; the old REJECTED Application and its full status history are
never mutated or deleted -- FR-18 auditability). A student may NOT have
two simultaneously open (non-terminal-status) Applications to the same
scholarship at once -- enforced in create_draft() below.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.accounts.models import ProviderProfile, StudentProfile
from apps.audit.models import AuditLog
from apps.audit.services import log_event
from apps.common.enums import ApplicationStatus, EligibilityStatus
from apps.scholarships.models import Scholarship

from ..models import Application, ApplicationStatusHistory

_TERMINAL_STATUSES = {ApplicationStatus.APPROVED, ApplicationStatus.REJECTED}


class ApplicationService:
    """Deterministic application lifecycle management (FR-09)."""

    #: The only forward transitions FR-09's status flow allows. Defined
    #: here now (even though transition logic isn't implemented yet) so
    #: every later caller reads the same source of truth instead of each
    #: view hard-coding its own allowed-transition list.
    ALLOWED_TRANSITIONS = {
        ApplicationStatus.DRAFT: {ApplicationStatus.SUBMITTED},
        ApplicationStatus.SUBMITTED: {ApplicationStatus.UNDER_REVIEW},
        ApplicationStatus.UNDER_REVIEW: {ApplicationStatus.SHORTLISTED, ApplicationStatus.REJECTED},
        ApplicationStatus.SHORTLISTED: {ApplicationStatus.APPROVED, ApplicationStatus.REJECTED},
        ApplicationStatus.APPROVED: set(),
        ApplicationStatus.REJECTED: set(),
    }

    # -------------------------------------------------------------
    # Ownership (the one choke point every view must use)
    # -------------------------------------------------------------

    @staticmethod
    def get_owned_application_or_403(application_id, student_profile: StudentProfile) -> Application:
        """Fetch an Application by id; 404 if it doesn't exist, 403 if
        it exists but belongs to a different student."""
        application = get_object_or_404(
            Application.objects.select_related("scholarship", "scholarship__provider", "student"),
            pk=application_id,
        )
        if application.student_id != student_profile.pk:
            raise PermissionDenied("You do not have access to this application.")
        return application

    @staticmethod
    def get_provider_managed_application_or_403(application_id, provider_profile: ProviderProfile) -> Application:
        """Fetch an Application by id for provider review; 404 if it
        doesn't exist, 403 if its scholarship belongs to a different
        provider (mirrors ScholarshipService.get_owned_scholarship_or_403,
        Prompt 3)."""
        application = get_object_or_404(
            Application.objects.select_related("scholarship", "scholarship__provider", "student", "student__user"),
            pk=application_id,
        )
        if application.scholarship.provider_id != provider_profile.pk:
            raise PermissionDenied("You do not have access to this application.")
        return application

    # -------------------------------------------------------------
    # Creation / draft
    # -------------------------------------------------------------

    @staticmethod
    def create_draft(*, student: StudentProfile, scholarship: Scholarship) -> Application:
        """
        Create a new DRAFT Application. Raises ValidationError if the
        student already has a non-terminal (DRAFT/SUBMITTED/
        UNDER_REVIEW/SHORTLISTED) Application for this exact scholarship
        -- re-applying after APPROVED/REJECTED is explicitly allowed
        (see module docstring "REAPPLICATION POLICY").
        """
        existing_open = Application.objects.filter(
            student=student, scholarship=scholarship,
        ).exclude(status__in=_TERMINAL_STATUSES)
        if existing_open.exists():
            raise ValidationError(
                "You already have an open application for this scholarship."
            )

        if not scholarship.is_visible_to_students:
            raise ValidationError(
                "This scholarship is not currently open for applications."
            )

        application = Application.objects.create(student=student, scholarship=scholarship)
        return application

    # -------------------------------------------------------------
    # Submission (FR-09 pipeline position: verify eligibility/readiness first)
    # -------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def submit(application: Application, actor) -> Application:
        """
        Transition DRAFT -> SUBMITTED. Verifies eligibility first via
        EligibilityService (blocks on NOT_ELIGIBLE, warns-but-allows on
        MISSING_INFO) and computes readiness via ReadinessService for
        the audit trail (informational only -- see module docstring
        "SUBMIT GATE"). Writes an ApplicationStatusHistory row and an
        AuditLog entry.
        """
        if application.status != ApplicationStatus.DRAFT:
            raise ValidationError(
                f"Cannot submit an application that is not in Draft status "
                f"(current status: {application.get_status_display()})."
            )

        # Imported locally to avoid a module-level circular import: this
        # is the ONE deliberate cross-app dependency FR-09's own pipeline
        # position requires (apps.applications -> apps.recommendations),
        # and apps.recommendations never imports apps.applications, so
        # the dependency graph stays a one-way DAG (see README "Import
        # graph" verification note for this prompt).
        from apps.recommendations.services.eligibility_service import EligibilityService
        from apps.recommendations.services.readiness_service import ReadinessService

        eligibility_results = EligibilityService.evaluate(application.student, application.scholarship)
        overall_status = EligibilityService.rollup_status(eligibility_results)

        if overall_status == EligibilityStatus.NOT_ELIGIBLE:
            raise ValidationError(
                "This application cannot be submitted: one or more mandatory "
                "eligibility requirements are not currently met."
            )

        readiness = ReadinessService.compute_readiness(application.student, application.scholarship)

        from_status = application.status
        application.status = ApplicationStatus.SUBMITTED
        application.submitted_at = timezone.now()
        application.save(update_fields=["status", "submitted_at", "updated_at"])

        note = f"Readiness at submission: {readiness.readiness_percent}%."
        if overall_status == EligibilityStatus.MISSING_INFO:
            note += " Submitted with incomplete profile information (Missing Information, not a hard block)."

        ApplicationStatusHistory.objects.create(
            application=application, from_status=from_status,
            to_status=ApplicationStatus.SUBMITTED, changed_by=actor, note=note,
        )
        log_event(
            actor=actor,
            event_type=AuditLog.EventType.APPLICATION_STATUS_CHANGED,
            related_object=application,
            description=f"Application for '{application.scholarship.title}' submitted. {note}",
        )
        return application

    # -------------------------------------------------------------
    # Provider-driven status transitions (FR-16)
    # -------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def transition_status(application: Application, to_status: str, actor, note: str = "") -> Application:
        """
        Provider-driven status transition (FR-16), validated against
        ALLOWED_TRANSITIONS above and logged to both
        ApplicationStatusHistory and apps.audit.AuditLog (FR-18).
        Raises ValidationError for any transition not present in
        ALLOWED_TRANSITIONS[application.status] -- e.g. skipping
        UNDER_REVIEW straight to APPROVED, or any change to an
        application already in a terminal state.
        """
        allowed = ApplicationService.ALLOWED_TRANSITIONS.get(application.status, set())
        if to_status not in allowed:
            raise ValidationError(
                f"Cannot transition from '{application.get_status_display()}' "
                f"to '{ApplicationStatus(to_status).label}'."
            )

        from_status = application.status
        application.status = to_status
        application.save(update_fields=["status", "updated_at"])

        ApplicationStatusHistory.objects.create(
            application=application, from_status=from_status,
            to_status=to_status, changed_by=actor, note=note,
        )
        log_event(
            actor=actor,
            event_type=AuditLog.EventType.APPLICATION_STATUS_CHANGED,
            related_object=application,
            description=f"Application for '{application.scholarship.title}' "
            f"moved from {from_status} to {to_status}."
            + (f" Note: {note}" if note else ""),
        )
        return application
