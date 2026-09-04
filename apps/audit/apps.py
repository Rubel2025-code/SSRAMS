from django.apps import AppConfig


class AuditConfig(AppConfig):
    """
    Owns: audit logging (FR-18). Every other app writes to this app's
    AuditLog model via apps.audit.services.log_event — this app never
    imports models from accounts/scholarships/recommendations/
    applications/ai_advisor at the model level to avoid a circular
    dependency (it accepts a generic actor/entity reference instead;
    see models.py).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    verbose_name = "Audit Logging"
