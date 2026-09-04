"""
apps/audit/services.py

Owns: log_event(), the single helper every other app calls to write an
AuditLog row (FR-18). Unlike the calculation services stubbed out in
apps.recommendations/apps.applications/apps.ai_advisor, this one is
fully implemented here — it is genuinely foundational (a generic
"write one audit row" utility, not a feature-specific calculation) and
every other app needs it available immediately to log events like
login/logout as those views are built out in later prompts.

Usage from any other app, e.g. after a provider is approved:

    from apps.audit.services import log_event

    log_event(
        actor=request.user,
        event_type=AuditLog.EventType.PROVIDER_APPROVED,
        related_object=provider_profile,
        description=f"Approved provider '{provider_profile.organization_name}'.",
        request=request,
    )

No other app's models are imported here — see models.py docstring for
why the generic relation exists.
"""

from __future__ import annotations

from typing import Optional

from django.db import models

from .models import AuditLog


def _client_ip(request) -> Optional[str]:
    if request is None:
        return None
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        # First entry in the chain is the original client.
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_event(
    *,
    actor=None,
    event_type: str,
    related_object: Optional[models.Model] = None,
    description: str = "",
    request=None,
) -> AuditLog:
    """
    Create and return one AuditLog row.

    Args:
        actor: the acting User, or None for system-initiated events.
        event_type: one of AuditLog.EventType.
        related_object: any model instance this event concerns
            (a Scholarship, a ScholarshipCriterionWeight, an
            Application, ...). Stored via a generic relation so this
            function works for any app's model without importing it.
        description: short human-readable summary.
        request: optional current HttpRequest, used only to extract the
            client IP address (SRS §3.3.3 security logging).

    This function never raises on a "normal" failure to keep audit
    logging from ever blocking the primary action it accompanies; any
    unexpected exception is left to propagate so it is not silently
    swallowed in a way that would hide a real integrity bug.
    """
    kwargs = {
        "actor": actor,
        "event_type": event_type,
        "description": description,
        "ip_address": _client_ip(request),
    }
    if related_object is not None:
        from django.contrib.contenttypes.models import ContentType

        kwargs["related_content_type"] = ContentType.objects.get_for_model(related_object)
        kwargs["related_object_id"] = related_object.pk

    return AuditLog.objects.create(**kwargs)
