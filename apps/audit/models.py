"""
apps/audit/models.py

Owns: AuditLog (FR-18: "records security- and admin-relevant events
[...] with user, action, timestamp, and related entity").

DESIGN NOTE — why a generic relation instead of per-app FKs:
FR-18 lists login/logout, provider approval, scholarship changes,
weight changes, status changes, and suspensions as events to log — i.e.
events belonging to accounts, scholarships, and applications models.
If AuditLog had a direct ForeignKey to each of those, apps.audit would
import from every other app, and (since accounts/scholarships/
applications don't need to import audit back) that's a one-way
dependency and not circular by itself — but it does mean audit could
never be tested or migrated independently of every other app, and every
new "loggable" model in a later prompt would require a migration on
this app too. Using Django's built-in contenttypes generic relation
instead lets any model in any app become "the related entity" without
apps.audit importing that app's models module, keeping the dependency
one-way in the direction that actually matters (other apps call
apps.audit.services.log_event; audit never imports them).
"""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.common.models import TimeStampedModel


class AuditLog(TimeStampedModel):
    """One row per security- or admin-relevant event (FR-18)."""

    class EventType(models.TextChoices):
        LOGIN = "login", "Login"
        LOGOUT = "logout", "Logout"
        ACCOUNT_REGISTERED = "account_registered", "Account Registered"
        PROFILE_UPDATED = "profile_updated", "Profile Updated"
        PROVIDER_APPROVED = "provider_approved", "Provider Approved"
        PROVIDER_REJECTED = "provider_rejected", "Provider Rejected"
        SCHOLARSHIP_CREATED = "scholarship_created", "Scholarship Created"
        SCHOLARSHIP_UPDATED = "scholarship_updated", "Scholarship Updated"
        SCHOLARSHIP_PUBLISHED = "scholarship_published", "Scholarship Published"
        SCHOLARSHIP_UNPUBLISHED = "scholarship_unpublished", "Scholarship Unpublished"
        SCHOLARSHIP_CLOSED = "scholarship_closed", "Scholarship Deactivated/Closed"
        SCHOLARSHIP_MODERATED = "scholarship_moderated", "Scholarship Moderated by Administrator"
        CRITERION_CREATED = "criterion_created", "Eligibility Criterion Created"
        CRITERION_UPDATED = "criterion_updated", "Eligibility Criterion Updated"
        CRITERION_DELETED = "criterion_deleted", "Eligibility Criterion Deleted"
        WEIGHT_CHANGED = "weight_changed", "Criterion Weight Changed"
        APPLICATION_STATUS_CHANGED = "application_status_changed", "Application Status Changed"
        ACCOUNT_SUSPENDED = "account_suspended", "Account Suspended"
        ACCOUNT_REACTIVATED = "account_reactivated", "Account Reactivated"
        OTHER = "other", "Other"

    actor = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
        help_text="The user who performed the action. Null for system-initiated events.",
    )
    event_type = models.CharField(max_length=40, choices=EventType.choices)
    description = models.CharField(max_length=500, blank=True)

    # Generic relation to "the related entity" (FR-18) — e.g. the
    # Scholarship that was published, the ScholarshipCriterionWeight
    # that changed, the Application whose status changed.
    related_content_type = models.ForeignKey(
        ContentType, on_delete=models.SET_NULL, null=True, blank=True
    )
    related_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    related_object = GenericForeignKey("related_content_type", "related_object_id")

    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        # "-id" is a tie-breaker, not a second sort concept. auto_now_add
        # resolution is coarser than a request on some platforms (Windows
        # clocks tick at ~15ms), so several audit rows written during one
        # request share an identical created_at; without the pk tie-break
        # "most recent first" is decided arbitrarily by the database,
        # which is unacceptable for an audit trail (FR-18).
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["event_type"]),
            models.Index(fields=["actor"]),
            models.Index(fields=["related_content_type", "related_object_id"]),
        ]

    def __str__(self):
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.get_event_type_display()} by {self.actor}"
