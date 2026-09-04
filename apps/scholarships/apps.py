from django.apps import AppConfig


class ScholarshipsConfig(AppConfig):
    """
    Owns: scholarships, eligibility criteria, provider-defined
    scholarship-specific weights, and scholarship management (FR-04, FR-05).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.scholarships"
    verbose_name = "Scholarships & Criteria"
