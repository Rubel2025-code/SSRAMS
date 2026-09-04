"""
apps/applications/views.py

Prompt 1 left a single placeholder view here. Prompt 6 replaces it with
the full FR-09/FR-13/FR-16 workflow: student apply/submit/track,
provider review, and bookmark toggle.

Views stay thin: parse the request, call ApplicationService (or the
model directly for simple reads/toggles), render. No eligibility/
readiness computation is duplicated here -- ApplicationService.submit()
already calls apps.recommendations internally exactly once.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from apps.common.enums import RoleChoices
from apps.common.rbac import role_required
from apps.scholarships.models import Scholarship

from .models import Application, Bookmark
from .services import ApplicationService


# ---------------------------------------------------------------------
# Student: apply / submit / track (FR-09)
# ---------------------------------------------------------------------

class MyApplicationsView(LoginRequiredMixin, ListView):
    """
    Replaces Prompt 1's placeholder. Preserves the exact same URL name
    (applications:my_applications) and context_object_name
    ("applications") the Prompt 1 template already used, so no existing
    link (navbar) needed to change and the pre-existing template
    structure keeps working (extended, not replaced, below).
    """

    template_name = "applications/my_applications.html"
    context_object_name = "applications"

    def get_queryset(self):
        if self.request.user.role != RoleChoices.STUDENT:
            raise PermissionDenied("Only students have an application list.")
        profile = getattr(self.request.user, "student_profile", None)
        if profile is None:
            return Application.objects.none()
        return (
            Application.objects.filter(student=profile)
            .select_related("scholarship", "scholarship__provider")
            .order_by("-created_at", "-id")
        )


@role_required(RoleChoices.STUDENT)
def apply_to_scholarship(request, scholarship_pk):
    """
    Creates a DRAFT Application for the requesting student against one
    scholarship (FR-09). GET shows a confirmation page (readiness +
    eligibility preview, reusing Prompt 4/5 services exactly as they
    already exist -- no new calculation here); POST creates the draft.
    """
    profile = getattr(request.user, "student_profile", None)
    if profile is None:
        messages.error(request, "Your student profile could not be found.")
        return redirect("dashboard:home")

    scholarship = get_object_or_404(
        Scholarship.objects.select_related("provider").prefetch_related("criteria__weight"),
        pk=scholarship_pk,
    )
    if not scholarship.is_visible_to_students:
        from django.http import Http404

        raise Http404("This scholarship is not currently available.")

    if request.method == "POST":
        try:
            application = ApplicationService.create_draft(student=profile, scholarship=scholarship)
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect("scholarships:detail", pk=scholarship.pk)
        messages.success(request, "Application draft created.")
        return redirect("applications:detail", pk=application.pk)

    from apps.recommendations.services.eligibility_service import EligibilityService
    from apps.recommendations.services.readiness_service import ReadinessService

    eligibility_results = EligibilityService.evaluate(profile, scholarship)
    overall_status = EligibilityService.rollup_status(eligibility_results)
    readiness = ReadinessService.compute_readiness(profile, scholarship)

    return render(
        request, "applications/apply_confirm.html",
        {
            "scholarship": scholarship,
            "overall_status": overall_status,
            "eligibility_results": eligibility_results,
            "readiness": readiness,
        },
    )


@role_required(RoleChoices.STUDENT)
def application_detail(request, pk):
    """Student's own view of one Application's status/history (FR-09:
    'students view current status'). Ownership enforced through
    ApplicationService's one choke point -- no id is trusted without it."""
    profile = getattr(request.user, "student_profile", None)
    if profile is None:
        messages.error(request, "Your student profile could not be found.")
        return redirect("dashboard:home")

    application = ApplicationService.get_owned_application_or_403(pk, profile)
    return render(
        request, "applications/application_detail.html",
        {"application": application, "history": application.status_history.all()},
    )


@role_required(RoleChoices.STUDENT)
def submit_application(request, pk):
    profile = getattr(request.user, "student_profile", None)
    if profile is None:
        messages.error(request, "Your student profile could not be found.")
        return redirect("dashboard:home")

    application = ApplicationService.get_owned_application_or_403(pk, profile)

    if request.method == "POST":
        try:
            ApplicationService.submit(application, actor=request.user)
            messages.success(request, "Your application has been submitted.")
        except ValidationError as exc:
            messages.error(request, str(exc))
    return redirect("applications:detail", pk=application.pk)


# ---------------------------------------------------------------------
# Provider: review applications (FR-16)
# ---------------------------------------------------------------------

@role_required(RoleChoices.PROVIDER)
def provider_application_list(request):
    profile = getattr(request.user, "provider_profile", None)
    applications = (
        Application.objects.filter(scholarship__provider=profile)
        .select_related("scholarship", "student", "student__user")
        .order_by("-updated_at", "-id")
        if profile else Application.objects.none()
    )
    return render(request, "applications/provider_list.html", {"applications": applications, "profile": profile})


@role_required(RoleChoices.PROVIDER)
def provider_application_detail(request, pk):
    profile = getattr(request.user, "provider_profile", None)
    if profile is None:
        raise PermissionDenied("Provider profile not found.")

    application = ApplicationService.get_provider_managed_application_or_403(pk, profile)
    allowed_next = sorted(ApplicationService.ALLOWED_TRANSITIONS.get(application.status, set()))

    if request.method == "POST":
        to_status = request.POST.get("to_status", "")
        note = request.POST.get("note", "").strip()
        try:
            ApplicationService.transition_status(application, to_status, actor=request.user, note=note)
            messages.success(request, f"Application status updated to {application.get_status_display()}.")
            return redirect("applications:provider_detail", pk=application.pk)
        except ValidationError as exc:
            messages.error(request, str(exc))

    return render(
        request, "applications/provider_detail.html",
        {"application": application, "history": application.status_history.all(), "allowed_next": allowed_next},
    )


# ---------------------------------------------------------------------
# Bookmarks (FR-13)
# ---------------------------------------------------------------------

@role_required(RoleChoices.STUDENT)
@require_POST
def toggle_bookmark(request, scholarship_pk):
    """Toggle a bookmark for one scholarship on/off for the requesting
    student (FR-13). POST-only action (it mutates data, so it must not be
    reachable by GET); redirects back to wherever the request came from
    (or the scholarship detail page)."""
    profile = getattr(request.user, "student_profile", None)
    if profile is None:
        messages.error(request, "Your student profile could not be found.")
        return redirect("dashboard:home")

    scholarship = get_object_or_404(Scholarship, pk=scholarship_pk)

    bookmark = Bookmark.objects.filter(student=profile, scholarship=scholarship).first()
    if bookmark:
        bookmark.delete()
        messages.success(request, "Removed from bookmarks.")
    else:
        Bookmark.objects.create(student=profile, scholarship=scholarship)
        messages.success(request, "Added to bookmarks.")

    # "next" (and the Referer header) are attacker-controllable, so they are
    # validated against this site's own hosts before being used as a redirect
    # target -- otherwise this view is an open redirect that can be used to
    # bounce a logged-in student to a look-alike phishing page.
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect("scholarships:detail", pk=scholarship.pk)


class MyBookmarksView(LoginRequiredMixin, ListView):
    template_name = "applications/my_bookmarks.html"
    context_object_name = "bookmarks"

    def get_queryset(self):
        if self.request.user.role != RoleChoices.STUDENT:
            raise PermissionDenied("Only students have a bookmark list.")
        profile = getattr(self.request.user, "student_profile", None)
        if profile is None:
            return Bookmark.objects.none()
        return (
            Bookmark.objects.filter(student=profile)
            .select_related("scholarship", "scholarship__provider")
            .order_by("-created_at", "-id")
        )
