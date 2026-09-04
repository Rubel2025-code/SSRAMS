"""
Role-based access control (RBAC) foundation shared by every app.

This module gives later prompts a single, consistent way to restrict a
view to a role (or set of roles) instead of each app inventing its own
"if request.user.role == ..." checks scattered through views (project
brief Step 12: "Establish role-based authorization foundation").

Usage in a later prompt's views.py:

    from apps.common.rbac import role_required
    from apps.common.enums import RoleChoices

    @role_required(RoleChoices.PROVIDER)
    def create_scholarship(request):
        ...

or, for class-based views:

    from apps.common.rbac import RoleRequiredMixin

    class CreateScholarshipView(RoleRequiredMixin, CreateView):
        allowed_roles = [RoleChoices.PROVIDER]
        ...
"""

from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, resolve_url


def role_required(*allowed_roles):
    """Restrict a function-based view to users with one of ``allowed_roles``."""

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if request.user.role not in allowed_roles:
                raise PermissionDenied(
                    "You do not have permission to access this page."
                )
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


def verified_provider_required(view_func):
    """
    Restrict a view to Scholarship Providers whose verification status is
    APPROVED (FR-03: "only admin-approved providers may publish
    scholarships"). Used by scholarship-management views in a later prompt.
    """

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        from apps.common.enums import ProviderVerificationStatus, RoleChoices

        if request.user.role != RoleChoices.PROVIDER:
            raise PermissionDenied("Only scholarship providers can access this page.")

        profile = getattr(request.user, "provider_profile", None)
        if profile is None or profile.verification_status != ProviderVerificationStatus.APPROVED:
            messages.warning(
                request,
                "Your provider account is not yet verified. "
                "This page is unavailable until an administrator approves your account.",
            )
            return redirect("dashboard:home")
        return view_func(request, *args, **kwargs)

    return _wrapped


class RoleRequiredMixin:
    """
    Class-based-view mixin equivalent of :func:`role_required`.

    Subclasses set ``allowed_roles = [RoleChoices.STUDENT, ...]``.
    """

    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            # Resolve settings.LOGIN_URL instead of hard-coding
            # "/accounts/login/", so this keeps working if the accounts
            # URLs are ever mounted under a different prefix. "next" is
            # Django's own REDIRECT_FIELD_NAME, which LoginView reads.
            login_url = resolve_url(settings.LOGIN_URL)
            return redirect(f"{login_url}?{urlencode({'next': request.get_full_path()})}")
        if self.allowed_roles and request.user.role not in self.allowed_roles:
            raise PermissionDenied("You do not have permission to access this page.")
        return super().dispatch(request, *args, **kwargs)


class OwnerRequiredMixin:
    """
    Class-based-view mixin that enforces object-level ownership, e.g. a
    provider may only review applications for scholarships they own, and
    a student may only view their own application (project brief Step 12:
    "Protection against unauthorized object access"; SRS §3.3.3).

    Subclasses implement ``get_owner(self, obj)`` returning the ``User``
    that should be allowed access, and set ``owner_field`` as a shortcut
    when the owning user is a direct/related attribute.

    Set ``allow_staff_override = True`` on a subclass to let ``is_staff``
    users through. It is OFF by default so this mixin matches the
    ownership choke points the service layer already uses
    (ScholarshipService.get_owned_scholarship_or_403,
    ApplicationService.get_owned_application_or_403), none of which grant
    an implicit staff bypass: an administrator reaches other users' data
    only through views explicitly gated on ``role_required(ADMIN)``, never
    by virtue of a Django Admin flag.
    """

    owner_field = None
    allow_staff_override = False

    def get_owner(self, obj):
        if self.owner_field is None:
            raise NotImplementedError(
                "Set 'owner_field' or override get_owner() on this view."
            )
        owner = obj
        for part in self.owner_field.split("__"):
            owner = getattr(owner, part)
        return owner

    def check_ownership(self, obj):
        if self.get_owner(obj) == self.request.user:
            return
        if self.allow_staff_override and self.request.user.is_staff:
            return
        raise PermissionDenied("You do not have access to this object.")
