"""
apps/accounts/views.py

Prompt 1 established login/logout/register-account views. Prompt 2 adds:
  - profile view/edit for both Student and Provider (FR-02, FR-03),
    with ownership enforced via apps.common.rbac.RoleRequiredMixin +
    OwnerRequiredMixin (no new authorization system introduced)
  - the administrator provider-verification review workflow (FR-03,
    FR-17), reusing apps.common.rbac.role_required and the existing
    ProviderVerificationStatus values
  - audit logging (FR-18) via the existing apps.audit.services.log_event
    for registration, login, logout, profile updates, and verification
    decisions

Views stay thin: parsing the request and calling forms/services. No
recommendation/eligibility/scholarship business logic is implemented
here (project brief SS15/SS16) -- this module only manages identity,
roles, and the student's/provider's own authoritative profile data.
"""

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.views import LoginView
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.audit.models import AuditLog
from apps.audit.services import log_event
from apps.common.enums import ProviderVerificationStatus, RoleChoices
from apps.common.rbac import role_required

from .forms import (
    ProviderProfileForm,
    ProviderRegistrationForm,
    ProviderVerificationDecisionForm,
    SSRAMSLoginForm,
    StudentProfileForm,
    StudentRegistrationForm,
)
from .models import ProviderProfile


# ---------------------------------------------------------------------
# Login / logout (Prompt 1 behavior preserved; audit logging added)
# ---------------------------------------------------------------------

class SSRAMSLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = SSRAMSLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        log_event(
            actor=self.request.user,
            event_type=AuditLog.EventType.LOGIN,
            description="User logged in.",
            request=self.request,
        )
        return response


@require_POST
def logout_view(request):
    """
    Log the current user out.

    POST-only: logging out mutates server-side state (it destroys the
    session and writes an AuditLog row), so it must not be reachable by a
    plain GET. A GET-triggered logout can be fired by any third-party page
    that embeds <img src="/accounts/logout/">, and it is exactly the kind
    of destructive-operation-over-GET the security review forbids. The
    navbar therefore submits a small CSRF-protected POST form rather than
    linking here (templates/base/navbar.html).
    """
    if request.user.is_authenticated:
        log_event(
            actor=request.user,
            event_type=AuditLog.EventType.LOGOUT,
            description="User logged out.",
            request=request,
        )
    auth_logout(request)
    return redirect("accounts:login")


class RegisterChoiceView(TemplateView):
    """Landing page letting a new user pick Student or Provider registration."""

    template_name = "accounts/register_choice.html"

    def dispatch(self, request, *args, **kwargs):
        # An already-authenticated user has nothing to register for;
        # send them to their dashboard instead of a confusing choice page.
        if request.user.is_authenticated:
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)


def register_student(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")

    if request.method == "POST":
        form = StudentRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            auth_login(request, user)
            log_event(
                actor=user,
                event_type=AuditLog.EventType.ACCOUNT_REGISTERED,
                related_object=user,
                description="Student account registered.",
                request=request,
            )
            messages.success(request, "Welcome to SSRAMS! Your student profile has been created.")
            return redirect("dashboard:home")
    else:
        form = StudentRegistrationForm()
    return render(request, "accounts/register_student.html", {"form": form})


def register_provider(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")

    if request.method == "POST":
        form = ProviderRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            auth_login(request, user)
            log_event(
                actor=user,
                event_type=AuditLog.EventType.ACCOUNT_REGISTERED,
                related_object=user,
                description="Provider account registered; verification is pending.",
                request=request,
            )
            messages.success(
                request,
                "Welcome to SSRAMS! Your provider account was created and is "
                "pending administrator verification.",
            )
            return redirect("dashboard:home")
    else:
        form = ProviderRegistrationForm()
    return render(request, "accounts/register_provider.html", {"form": form})


# ---------------------------------------------------------------------
# Student profile (FR-02) -- view + edit, ownership enforced by the
# view only ever operating on request.user's own profile (there is no
# ID in the URL to manipulate -- see README "Ownership" note below).
# ---------------------------------------------------------------------

@role_required(RoleChoices.STUDENT)
def student_profile_view(request):
    profile = getattr(request.user, "student_profile", None)
    if profile is None:
        messages.error(request, "Your student profile could not be found.")
        return redirect("dashboard:home")
    return render(request, "accounts/student_profile.html", {"profile": profile})


@role_required(RoleChoices.STUDENT)
def student_profile_edit(request):
    profile = getattr(request.user, "student_profile", None)
    if profile is None:
        messages.error(request, "Your student profile could not be found.")
        return redirect("dashboard:home")

    if request.method == "POST":
        form = StudentProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            log_event(
                actor=request.user,
                event_type=AuditLog.EventType.PROFILE_UPDATED,
                related_object=profile,
                description="Student profile updated.",
                request=request,
            )
            messages.success(request, "Your profile has been updated.")
            return redirect("accounts:student_profile")
    else:
        form = StudentProfileForm(instance=profile)
    return render(request, "accounts/student_profile_form.html", {"form": form, "profile": profile})


# ---------------------------------------------------------------------
# Provider profile (FR-03) -- same ownership pattern as student profile.
# ---------------------------------------------------------------------

@role_required(RoleChoices.PROVIDER)
def provider_profile_view(request):
    profile = getattr(request.user, "provider_profile", None)
    if profile is None:
        messages.error(request, "Your provider profile could not be found.")
        return redirect("dashboard:home")
    return render(request, "accounts/provider_profile.html", {"profile": profile})


@role_required(RoleChoices.PROVIDER)
def provider_profile_edit(request):
    profile = getattr(request.user, "provider_profile", None)
    if profile is None:
        messages.error(request, "Your provider profile could not be found.")
        return redirect("dashboard:home")

    if request.method == "POST":
        form = ProviderProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            log_event(
                actor=request.user,
                event_type=AuditLog.EventType.PROFILE_UPDATED,
                related_object=profile,
                description="Provider profile updated.",
                request=request,
            )
            messages.success(request, "Your organization profile has been updated.")
            return redirect("accounts:provider_profile")
    else:
        form = ProviderProfileForm(instance=profile)
    return render(request, "accounts/provider_profile_form.html", {"form": form, "profile": profile})


# ---------------------------------------------------------------------
# Administrator: provider verification review (FR-03, FR-17).
#
# Ownership/authorization note: these two views are the ones where an
# ID *does* appear in the URL (which ProviderProfile to review), so
# object-level access is enforced explicitly (get_object_or_404 + the
# role_required(ADMIN) gate) rather than relying on "there's no ID to
# manipulate" as with the student/provider's own profile views above.
# ---------------------------------------------------------------------

@role_required(RoleChoices.ADMIN)
def admin_verification_list(request):
    pending = ProviderProfile.objects.filter(
        verification_status=ProviderVerificationStatus.PENDING
    ).select_related("user").order_by("created_at", "id")
    reviewed = ProviderProfile.objects.exclude(
        verification_status=ProviderVerificationStatus.PENDING
    ).select_related("user").order_by("-updated_at", "-id")[:25]
    return render(
        request,
        "accounts/admin_verification_list.html",
        {"pending_providers": pending, "reviewed_providers": reviewed},
    )


@role_required(RoleChoices.ADMIN)
def admin_verification_detail(request, provider_id):
    provider_profile = get_object_or_404(ProviderProfile, pk=provider_id)
    latest_submission = provider_profile.verification_history.first()

    if request.method == "POST":
        form = ProviderVerificationDecisionForm(request.POST)
        if form.is_valid():
            decision = form.cleaned_data["decision"]
            note = form.cleaned_data["decision_note"]

            # Record the decision on the specific submission being
            # reviewed (FR-03: auditable per-submission history), then
            # sync the denormalized "current state" field on the profile
            # (see ProviderProfile model docstring: written only here,
            # never directly by provider-facing views).
            if latest_submission is not None:
                latest_submission.status = decision
                latest_submission.decided_by = request.user
                latest_submission.decision_note = note
                latest_submission.decided_at = timezone.now()
                latest_submission.save()

            provider_profile.verification_status = decision
            provider_profile.save(update_fields=["verification_status", "updated_at"])

            event_type = (
                AuditLog.EventType.PROVIDER_APPROVED
                if decision == ProviderVerificationStatus.APPROVED
                else AuditLog.EventType.PROVIDER_REJECTED
            )
            log_event(
                actor=request.user,
                event_type=event_type,
                related_object=provider_profile,
                description=f"Provider '{provider_profile.organization_name}' {decision} by admin.",
                request=request,
            )
            messages.success(
                request, f"Provider '{provider_profile.organization_name}' has been {decision}."
            )
            return redirect("accounts:admin_verification_list")
    else:
        form = ProviderVerificationDecisionForm()

    return render(
        request,
        "accounts/admin_verification_detail.html",
        {
            "provider_profile": provider_profile,
            "latest_submission": latest_submission,
            "history": provider_profile.verification_history.all(),
            "form": form,
        },
    )
