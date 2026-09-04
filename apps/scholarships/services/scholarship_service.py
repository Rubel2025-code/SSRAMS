"""
apps/scholarships/services/scholarship_service.py

Owns: Scholarship record CRUD and ownership checks (FR-04).

Ownership enforcement lives here (not duplicated per-view) so "Provider
A cannot touch Provider B's scholarship" is guaranteed at one choke
point. Views call ``get_owned_scholarship_or_403`` for every
edit/delete/criteria/weight/publish action rather than fetching the
Scholarship directly with a bare ``get_object_or_404``.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from apps.accounts.models import ProviderProfile
from apps.audit.models import AuditLog
from apps.audit.services import log_event

from ..models import Scholarship


class ScholarshipService:
    @staticmethod
    def get_owned_scholarship_or_403(scholarship_id, provider_profile: ProviderProfile) -> Scholarship:
        """
        Fetch a Scholarship by id, raising Http404 if it doesn't exist
        and PermissionDenied (403) if it exists but belongs to a
        different provider. This is the single choke point every
        provider-facing scholarship/criteria/weight view must use
        instead of a bare get_object_or_404 (project brief §3: "Provider
        A must not be able to edit/delete/modify Provider B's
        scholarship/criteria/weights" — enforced server-side, not by
        hidden UI).
        """
        scholarship = get_object_or_404(Scholarship, pk=scholarship_id)
        if scholarship.provider_id != provider_profile.pk:
            raise PermissionDenied("You do not have access to this scholarship.")
        return scholarship

    @staticmethod
    def create_scholarship(*, provider_profile: ProviderProfile, actor, **fields) -> Scholarship:
        """
        Create a new Scholarship owned by ``provider_profile``. Always
        starts unpublished (Scholarship.is_published defaults to False)
        — publishing is a separate, explicit action gated by
        PublicationService, never implied by creation.
        """
        scholarship = Scholarship.objects.create(provider=provider_profile, **fields)
        log_event(
            actor=actor,
            event_type=AuditLog.EventType.SCHOLARSHIP_CREATED,
            related_object=scholarship,
            description=f"Scholarship '{scholarship.title}' created.",
        )
        return scholarship

    @staticmethod
    def update_scholarship(*, scholarship: Scholarship, actor, **fields) -> Scholarship:
        """
        Update a Scholarship's own descriptive fields. Caller is
        responsible for having already resolved ``scholarship`` through
        get_owned_scholarship_or_403 — this method does not re-check
        ownership so it can also be used by administrator moderation
        flows, which are intentionally broader (project brief §13).
        """
        for field, value in fields.items():
            setattr(scholarship, field, value)
        scholarship.save()
        log_event(
            actor=actor,
            event_type=AuditLog.EventType.SCHOLARSHIP_UPDATED,
            related_object=scholarship,
            description=f"Scholarship '{scholarship.title}' updated.",
        )
        return scholarship

    @staticmethod
    def set_active(*, scholarship: Scholarship, is_active: bool, actor, note: str = "") -> Scholarship:
        """
        Deactivate/reactivate a scholarship (FR-17 moderation, or a
        provider withdrawing their own listing) without deleting it or
        its history. Distinct from publish/unpublish — see the
        "Lifecycle" section of models.py's module docstring.
        """
        scholarship.is_active = is_active
        scholarship.save(update_fields=["is_active", "updated_at"])
        event_type = (
            AuditLog.EventType.SCHOLARSHIP_UPDATED
            if is_active
            else AuditLog.EventType.SCHOLARSHIP_CLOSED
        )
        log_event(
            actor=actor,
            event_type=event_type,
            related_object=scholarship,
            description=(
                f"Scholarship '{scholarship.title}' "
                f"{'reactivated' if is_active else 'deactivated'}."
                + (f" Note: {note}" if note else "")
            ),
        )
        return scholarship

    @staticmethod
    def delete_scholarship(*, scholarship: Scholarship, actor) -> None:
        """
        Delete a Scholarship (and, via CASCADE, its criteria/weights).
        Only permitted on scholarships that have never been published —
        once published, a provider must deactivate rather than delete,
        so any student who bookmarked/applied to it doesn't lose the
        record out from under them. This rule is enforced here (the one
        place scholarships are ever deleted), not just in the view.
        """
        if scholarship.is_published:
            raise PermissionDenied(
                "A published scholarship cannot be deleted. Deactivate it instead."
            )
        title = scholarship.title
        log_event(
            actor=actor,
            event_type=AuditLog.EventType.SCHOLARSHIP_UPDATED,
            description=f"Draft scholarship '{title}' deleted.",
        )
        scholarship.delete()

    @staticmethod
    def moderate(*, scholarship: Scholarship, is_active: bool, actor, reason: str = "") -> Scholarship:
        """
        Administrator moderation action (project brief §13): deactivate
        or reactivate ANY scholarship regardless of owning provider.
        Distinct from set_active only in which AuditLog.EventType it
        records — SCHOLARSHIP_MODERATED always, so moderation actions
        are distinguishable in the audit trail from a provider managing
        their own listing. Callers (admin views) are responsible for
        the role_required(ADMIN) gate; this method does not itself
        check the actor's role, matching update_scholarship's pattern
        of leaving authorization to the view layer for admin flows.
        """
        scholarship.is_active = is_active
        scholarship.save(update_fields=["is_active", "updated_at"])
        log_event(
            actor=actor,
            event_type=AuditLog.EventType.SCHOLARSHIP_MODERATED,
            related_object=scholarship,
            description=(
                f"Administrator {'reactivated' if is_active else 'deactivated'} "
                f"scholarship '{scholarship.title}'."
                + (f" Reason: {reason}" if reason else "")
            ),
        )
        return scholarship
