from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """
    Owns: authentication, the custom User model, roles, Student/Provider
    profiles, and provider verification (FR-01, FR-02, FR-03).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = "Accounts (Auth, Roles & Profiles)"
