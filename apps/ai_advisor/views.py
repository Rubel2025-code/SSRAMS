"""
apps/ai_advisor/views.py

Prompt 1 left a single placeholder view here (proving the `ai_advisor`
namespace was wired end-to-end). Prompt 7 replaces it with the three
real student-facing AI surfaces: the Context-Aware AI Advisor (FR-10),
the AI Strategy Planner (FR-22), and the Profile Improvement Advisor
(FR-23).

Views stay thin: call AIAdvisorService, render. No Facts Bundle
assembly, no Gemini call, and no eligibility/readiness/score logic
appears anywhere in this file (Prompt 7 SS10) -- AIAdvisorService is
the only module views call.

All three views operate ONLY on request.user's own StudentProfile --
no student id ever appears in any URL in this file (same ownership-by-
construction pattern every student-facing view in this project has used
since Prompt 2). Uses LoginRequiredMixin, matching the exact convention
apps.recommendations.views already established in Prompts 4-6, rather
than a hand-rolled dispatch() redirect.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.generic import TemplateView

from apps.common.enums import RoleChoices

from .models import AIInteraction
from .services.ai_advisor_service import AIAdvisorService


def _get_student_profile_or_403(request):
    if request.user.role != RoleChoices.STUDENT:
        raise PermissionDenied("AI advisory features are available to students only.")
    return getattr(request.user, "student_profile", None)


class AIAdvisorView(LoginRequiredMixin, TemplateView):
    """
    FR-10: Context-Aware AI Advisor. GET shows the question form plus
    this student's own past AI_ADVISOR interactions; POST asks a new
    question. Preserves the Prompt 1 URL name ("advisor") so no
    existing link needed to change.
    """

    template_name = "ai_advisor/advisor.html"

    def get(self, request, *args, **kwargs):
        profile = _get_student_profile_or_403(request)
        history = (
            AIInteraction.objects.filter(student=profile, interaction_type="ai_advisor")[:10]
            if profile else []
        )
        return render(request, self.template_name, {"profile_missing": profile is None, "history": history})

    def post(self, request, *args, **kwargs):
        profile = _get_student_profile_or_403(request)
        if profile is None:
            messages.error(request, "Your student profile could not be found.")
            return redirect("dashboard:home")

        question = request.POST.get("question", "").strip()
        if not question:
            messages.error(request, "Please enter a question.")
            return redirect("ai_advisor:advisor")

        AIAdvisorService.ask_advisor(profile, question)
        return redirect("ai_advisor:advisor")


class StrategyPlannerView(LoginRequiredMixin, TemplateView):
    """FR-22: AI Strategy Planner over the student's own current Top Opportunities."""

    template_name = "ai_advisor/strategy_planner.html"

    def get(self, request, *args, **kwargs):
        profile = _get_student_profile_or_403(request)
        if profile is None:
            return render(request, self.template_name, {"profile_missing": True, "interaction": None})

        latest = AIInteraction.objects.filter(student=profile, interaction_type="strategy_planner").first()
        return render(request, self.template_name, {"profile_missing": False, "interaction": latest})

    def post(self, request, *args, **kwargs):
        profile = _get_student_profile_or_403(request)
        if profile is None:
            messages.error(request, "Your student profile could not be found.")
            return redirect("dashboard:home")

        AIAdvisorService.generate_strategy(profile)
        return redirect("ai_advisor:strategy_planner")


class ProfileImprovementView(LoginRequiredMixin, TemplateView):
    """FR-23: Profile Improvement Advisor, catalog-wide."""

    template_name = "ai_advisor/profile_improvement.html"

    def get(self, request, *args, **kwargs):
        profile = _get_student_profile_or_403(request)
        if profile is None:
            return render(request, self.template_name, {"profile_missing": True, "interaction": None})

        latest = AIInteraction.objects.filter(student=profile, interaction_type="profile_improvement").first()
        return render(request, self.template_name, {"profile_missing": False, "interaction": latest})

    def post(self, request, *args, **kwargs):
        profile = _get_student_profile_or_403(request)
        if profile is None:
            messages.error(request, "Your student profile could not be found.")
            return redirect("dashboard:home")

        AIAdvisorService.generate_profile_improvement(profile)
        return redirect("ai_advisor:profile_improvement")
