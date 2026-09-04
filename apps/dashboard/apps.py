from django.apps import AppConfig


class DashboardConfig(AppConfig):
    """
    Owns: role-specific dashboard composition (student/provider/admin
    landing pages) and the site root redirect.

    This app deliberately has NO models of its own and NO migrations
    folder — it is read-only glue that composes data already owned by
    other apps (accounts, scholarships, applications, recommendations)
    for display. Business logic and models stay in the owning app; if a
    later prompt finds itself adding a model here, that is a signal the
    model belongs in a different app instead.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dashboard"
    verbose_name = "Dashboard"
