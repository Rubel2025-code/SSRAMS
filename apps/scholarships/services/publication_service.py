"""
apps/scholarships/services/publication_service.py

Owns: the publish/unpublish decision (FR-04, FR-03, FR-06). This is the
ONLY place Scholarship.is_published is ever set to True — every other
service/view that wants to publish a scholarship must go through here,
so the "weights valid AND provider verified" gate can never be
bypassed by a shortcut elsewhere.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError

from apps.audit.models import AuditLog
from apps.audit.services import log_event
from apps.common.enums import ProviderVerificationStatus

from ..models import Scholarship
from .weight_service import WeightService


class PublicationService:
    @staticmethod
    def publish(*, scholarship: Scholarship, actor) -> Scholarship:
        """
        Publish a scholarship. Raises ValidationError if the weighting
        configuration is invalid (FR-04/FR-06), or PermissionDenied if
        the owning provider is not admin-verified (FR-03). Both checks
        must pass — this method does not allow a fallback path around
        either one.
        """
        validation = WeightService.validate_configuration(scholarship)
        if not validation.is_valid:
            raise ValidationError(
                "This scholarship cannot be published: " + " ".join(validation.issues)
            )

        if scholarship.provider.verification_status != ProviderVerificationStatus.APPROVED:
            raise PermissionDenied(
                "This scholarship cannot be published because the provider "
                "account is not yet admin-verified (FR-03)."
            )

        scholarship.is_published = True
        scholarship.save(update_fields=["is_published", "updated_at"])

        log_event(
            actor=actor,
            event_type=AuditLog.EventType.SCHOLARSHIP_PUBLISHED,
            related_object=scholarship,
            description=f"Scholarship '{scholarship.title}' published.",
        )
        return scholarship

    @staticmethod
    def unpublish(*, scholarship: Scholarship, actor, note: str = "") -> Scholarship:
        """
        Withdraw a scholarship from student-facing discovery/scoring
        without deactivating or deleting it — a provider can still edit
        it and re-publish later. Always permitted for an already-owned
        scholarship (no weight/verification precondition to reverse a
        publish decision).
        """
        scholarship.is_published = False
        scholarship.save(update_fields=["is_published", "updated_at"])

        log_event(
            actor=actor,
            event_type=AuditLog.EventType.SCHOLARSHIP_UNPUBLISHED,
            related_object=scholarship,
            description=f"Scholarship '{scholarship.title}' unpublished."
            + (f" Note: {note}" if note else ""),
        )
        return scholarship
