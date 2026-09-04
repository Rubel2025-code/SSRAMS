"""
apps/applications/models.py

Owns: Application, ApplicationStatusHistory, Bookmark.

Design notes:
  - Application.status is the fast "current state" field every other
    app/view checks; ApplicationStatusHistory is the append-only audit
    trail FR-18 requires ("status changes" is explicitly listed as an
    audit-logged event type) and lets the applications tracker (FR-09:
    "students view current status") show a timeline, not just the
    latest value.
  - Bookmark is intentionally its own tiny model (not a boolean field on
    a join table) because FR-13 says a bookmark "shows name, deadline,
    current match score, and provider" — i.e. it is a first-class,
    student-curated list independent of application status.
"""

from django.db import models

from apps.accounts.models import StudentProfile, User
from apps.common.enums import ApplicationStatus, RoleChoices
from apps.common.models import TimeStampedModel
from apps.scholarships.models import Scholarship


class Application(TimeStampedModel):
    """
    A student's application to a scholarship (FR-09). Status flow:
    Draft -> Submitted -> Under Review -> Shortlisted -> Approved / Rejected.
    """

    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="applications"
    )
    scholarship = models.ForeignKey(
        Scholarship, on_delete=models.CASCADE, related_name="applications"
    )

    status = models.CharField(
        max_length=20, choices=ApplicationStatus.choices, default=ApplicationStatus.DRAFT
    )
    submitted_at = models.DateTimeField(
        null=True, blank=True, help_text="Set when status transitions to SUBMITTED."
    )

    # FR-09: "record ... required information" — the concrete set of
    # required fields/documents is scholarship-specific (driven by its
    # ScholarshipCriterion rows with criterion_type=DOCUMENTS, etc.), so
    # submitted answers/uploads are stored as structured JSON here rather
    # than as fixed columns. The document-upload UI itself is built in a
    # later prompt.
    submitted_data = models.JSONField(
        default=dict, blank=True,
        help_text="Structured record of the information/documents the "
        "student submitted for this application.",
    )

    class Meta:
        # No blanket unique_together on (student, scholarship): the SRS
        # does not state whether a student may reapply after Rejected, so
        # that policy decision is deliberately left to ApplicationService
        # in a later prompt rather than hard-coded as a DB constraint here.
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["student", "status"]),
            models.Index(fields=["student", "scholarship"]),
        ]
        ordering = ["-created_at", "-id"]  # "-id": deterministic tie-break, see AuditLog.Meta

    def __str__(self):
        return f"{self.student} -> {self.scholarship} [{self.get_status_display()}]"


class ApplicationStatusHistory(TimeStampedModel):
    """Append-only status-change log for one Application (FR-09, FR-18)."""

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="status_history"
    )
    from_status = models.CharField(max_length=20, choices=ApplicationStatus.choices, blank=True)
    to_status = models.CharField(max_length=20, choices=ApplicationStatus.choices)
    changed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    note = models.CharField(max_length=500, blank=True)

    class Meta:
        # Oldest first: this is a displayed timeline. "id" is the
        # tie-break (see AuditLog.Meta) so two transitions recorded in
        # the same clock tick always render in the order they happened.
        ordering = ["created_at", "id"]
        verbose_name_plural = "Application status histories"

    def __str__(self):
        return f"{self.application_id}: {self.from_status or '—'} -> {self.to_status}"


class Bookmark(TimeStampedModel):
    """A student-saved scholarship for later reference (FR-13)."""

    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="bookmarks"
    )
    scholarship = models.ForeignKey(
        Scholarship, on_delete=models.CASCADE, related_name="bookmarked_by"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["student", "scholarship"], name="unique_bookmark_per_student_scholarship")
        ]
        ordering = ["-created_at", "-id"]  # "-id": deterministic tie-break, see AuditLog.Meta

    def __str__(self):
        return f"{self.student} bookmarked {self.scholarship}"
