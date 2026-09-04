from django.apps import AppConfig


class ApplicationsConfig(AppConfig):
    """
    Owns: scholarship applications, the status lifecycle, provider
    review, and application tracking (FR-09, FR-16).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.applications"
    verbose_name = "Applications"
