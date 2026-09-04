"""Template context processor shared by every template (see config/settings.py TEMPLATES)."""

from django.conf import settings


def role_context(request):
    """
    Expose role flags and a couple of safe, non-secret settings to every
    template, so templates/base/navbar.html can render role-aware nav
    without each app's templates re-deriving the same thing.
    """
    return {
        "nav_is_student": getattr(request, "is_student", False),
        "nav_is_provider": getattr(request, "is_provider", False),
        "nav_is_admin_role": getattr(request, "is_admin_role", False),
        "site_name": "SSRAMS",
        "site_tagline": "Smart Scholarship Recommendation & Application Management System",
        "gemini_enabled": settings.GEMINI_ENABLED,
    }
