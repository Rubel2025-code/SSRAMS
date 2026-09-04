"""
apps/dashboard/views.py

Role-aware dashboard composition (project brief Step 9/FR-15). This app
owns no models and performs no business calculations of its own — it
only reads simple counts/latest-rows from other apps' models, OR calls
another app's own service for anything that requires real computation
(Prompt 5: DeadlineService.top_opportunities for the student dashboard's
Top Opportunities summary — this file does not rank/score anything
itself, it only asks apps.recommendations for an already-ranked list
and slices it for a compact preview). Any computation still lacking an
owning implementation is rendered only once that app implements it;
until then the relevant dashboard section shows a "coming in a later
prompt" placeholder rather than a fabricated number.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.views.generic import TemplateView

from apps.applications.models import Application
from apps.common.enums import RoleChoices
from apps.scholarships.models import Scholarship
from django.shortcuts import render


def landing(request):
    return render(request, "dashboard/landing.html")

class DashboardHomeView(LoginRequiredMixin, TemplateView):
    """
    Single entry point at /dashboard/ — dispatches to the correct
    role-specific template based on request.user.role (FR-15: "Student
    dashboards ... Provider dashboards ... Admin dashboards").
    """

    def get_template_names(self):
        role = self.request.user.role
        return {
            RoleChoices.STUDENT: "dashboard/student_dashboard.html",
            RoleChoices.PROVIDER: "dashboard/provider_dashboard.html",
            RoleChoices.ADMIN: "dashboard/admin_dashboard.html",
        }.get(role, "dashboard/student_dashboard.html")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        if user.role == RoleChoices.STUDENT:
            profile = getattr(user, "student_profile", None)
            context["profile"] = profile
            context["bookmark_count"] = profile.bookmarks.count() if profile else 0
            context["application_count"] = (
                Application.objects.filter(student=profile).count() if profile else 0
            )
            # Prompt 5: a compact (top-3) preview of the student's own
            # ranked Top Opportunities, reusing
            # apps.recommendations.services.DeadlineService.top_opportunities
            # exactly as computed for the dedicated Top Opportunities
            # page — this view does not rank/score anything itself,
            # only asks for the same authoritative list and slices it
            # (SS14: "Do not overload the dashboard with unnecessary
            # information" -> show 3, link to the full page for more).
            if profile is not None:
                from apps.recommendations.services.deadline_service import DeadlineService

                context["top_opportunities_preview"] = DeadlineService.top_opportunities(profile, limit=3)
            else:
                context["top_opportunities_preview"] = []

        elif user.role == RoleChoices.PROVIDER:
            profile = getattr(user, "provider_profile", None)
            context["profile"] = profile
            context["scholarship_count"] = (
                Scholarship.objects.filter(provider=profile).count() if profile else 0
            )
            context["published_count"] = (
                Scholarship.objects.filter(provider=profile, is_published=True).count()
                if profile else 0
            )
            # Prompt 6: count of submitted-or-later applications awaiting
            # this provider's attention (SUBMITTED/UNDER_REVIEW -- not
            # DRAFT, since a draft is the student's own unsubmitted work
            # and not yet visible to the provider; not
            # APPROVED/REJECTED/SHORTLISTED, since those already have a
            # provider decision recorded). Simple filtered .count(), no
            # new business logic -- matching this app's existing boundary.
            if profile is not None:
                from apps.common.enums import ApplicationStatus

                context["pending_application_count"] = Application.objects.filter(
                    scholarship__provider=profile,
                    status__in=[ApplicationStatus.SUBMITTED, ApplicationStatus.UNDER_REVIEW],
                ).count()
            else:
                context["pending_application_count"] = 0

        elif user.role == RoleChoices.ADMIN:
            context["total_students"] = _count_by_role(RoleChoices.STUDENT)
            context["total_providers"] = _count_by_role(RoleChoices.PROVIDER)
            context["total_scholarships"] = Scholarship.objects.count()
            context["total_applications"] = Application.objects.count()
            context["pending_verification_count"] = _pending_verification_count()

        return context


def _count_by_role(role):
    from apps.accounts.models import User

    return User.objects.filter(role=role).count()


def _pending_verification_count():
    from apps.accounts.models import ProviderProfile
    from apps.common.enums import ProviderVerificationStatus

    return ProviderProfile.objects.filter(
        verification_status=ProviderVerificationStatus.PENDING
    ).count()


def root_redirect(request):
    """
    Handles "/" (project brief Step 10: root redirects into the
    dashboard, which itself sends anonymous users to login via
    LoginRequiredMixin's LOGIN_URL).
    """
    return redirect("dashboard:home")
