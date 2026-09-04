"""
apps/recommendations/views.py

Prompt 4 added the student-facing recommendation list + detail views
(Match Score, eligibility, gaps). Prompt 5 extends both with Readiness
and Deadline information, and replaces the Top Opportunities
placeholder with a real implementation.

No Match Score or Eligibility calculation is duplicated here -- this
file only calls RecommendationService/EligibilityService (Prompt 4,
unchanged) and ReadinessService/DeadlineService (Prompt 5, new).

Views stay thin: call the services in apps.recommendations.services,
render. No scoring/eligibility/readiness/deadline logic is implemented
inline here.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, render
from django.views.generic import TemplateView

from apps.common.enums import RoleChoices
from apps.scholarships.models import Scholarship

from .services.deadline_service import DeadlineService
from .services.eligibility_service import EligibilityService
from .services.gap_service import GapService
from .services.readiness_service import ReadinessService
from .services.recommendation_service import RecommendationService


class TopOpportunitiesView(LoginRequiredMixin, TemplateView):
    """
    FR-20: ranked Top Opportunities for the requesting student's OWN
    profile only (SS19 -- no student id anywhere in this URL, same
    ownership-by-construction pattern as every other student-facing
    view in this app). Replaces Prompt 1's placeholder; the URL name
    (recommendations:top_opportunities) is unchanged, so no existing
    link (navbar, dashboards) needed to change.
    """

    template_name = "recommendations/top_opportunities.html"

    def get(self, request, *args, **kwargs):
        if request.user.role != RoleChoices.STUDENT:
            raise PermissionDenied("Only students can view Top Opportunities.")

        profile = getattr(request.user, "student_profile", None)
        if profile is None:
            return render(
                request, self.template_name,
                {"profile_missing": True, "opportunities": []},
            )

        opportunities = DeadlineService.top_opportunities(profile, limit=5)
        return render(
            request, self.template_name,
            {"profile_missing": False, "opportunities": opportunities},
        )


class MyRecommendationsView(LoginRequiredMixin, TemplateView):
    """
    Student-facing recommendation list (Prompt 4, extended in Prompt 5
    with readiness + deadline per row). A student sees Match Score +
    eligibility + readiness + deadline for every currently
    recommendable scholarship against their OWN profile only.
    """

    template_name = "recommendations/my_recommendations.html"

    def get(self, request, *args, **kwargs):
        if request.user.role != RoleChoices.STUDENT:
            raise PermissionDenied("Only students can view recommendations.")

        profile = getattr(request.user, "student_profile", None)
        if profile is None:
            return render(
                request, self.template_name,
                {"profile_missing": True, "rows": []},
            )

        recommendations = RecommendationService.rank_for_student(profile)

        # One readiness + deadline computation per already-ranked
        # scholarship (SS18: reuse Prompt 4's ranked result rather than
        # re-querying/re-ranking; each ReadinessService call does its
        # own single select_related('weight') fetch, same "no N+1"
        # shape as Prompt 4's own services).
        rows = []
        for recommendation in recommendations:
            scholarship = recommendation.scholarship
            readiness = ReadinessService.compute_readiness(profile, scholarship)
            rows.append({
                "recommendation": recommendation,
                "readiness": readiness,
                "days_remaining": DeadlineService.days_remaining(scholarship),
                "urgency": DeadlineService.urgency(scholarship),
            })

        return render(
            request, self.template_name,
            {"profile_missing": False, "rows": rows},
        )


class RecommendationDetailView(LoginRequiredMixin, TemplateView):
    """
    Full Match Score breakdown, per-criterion eligibility, gaps (Prompt
    4), plus Readiness and Deadline (Prompt 5) for ONE scholarship
    against the requesting student's OWN profile. The scholarship id is
    in the URL, but the student whose profile is scored is always
    request.user's own (SS19 -- there is no student id in this URL at
    all, only a scholarship id).
    """

    template_name = "recommendations/recommendation_detail.html"

    def get(self, request, *args, **kwargs):
        if request.user.role != RoleChoices.STUDENT:
            raise PermissionDenied("Only students can view their recommendation for a scholarship.")

        profile = getattr(request.user, "student_profile", None)
        if profile is None:
            return render(
                request, self.template_name,
                {"profile_missing": True, "scholarship": None},
            )

        scholarship = get_object_or_404(
            Scholarship.objects.select_related("provider").prefetch_related("criteria__weight"),
            pk=kwargs["pk"],
        )

        if not scholarship.is_visible_to_students:
            from django.http import Http404

            raise Http404("This scholarship is not currently available for recommendations.")

        try:
            recommendation = RecommendationService.compute_match_score(profile, scholarship)
            recommendation_error = None
        except ValidationError as exc:
            recommendation = None
            recommendation_error = str(exc)

        # NOTE on evaluation passes: compute_match_score(), evaluate(),
        # get_gaps(), and compute_readiness() each independently call
        # CriterionEvaluationService.evaluate_all() (via
        # EligibilityService) over the same criteria list for this one
        # scholarship. This is the same deliberate Prompt 4 tradeoff,
        # carried forward unchanged -- see recommendation_service.py's
        # and deadline_service.py's own "no N+1" notes for why this
        # remains acceptable at this data scale.
        eligibility_results = EligibilityService.evaluate(profile, scholarship)
        overall_status = EligibilityService.rollup_status(eligibility_results)
        gaps = GapService.get_gaps(profile, scholarship)
        readiness = ReadinessService.compute_readiness(profile, scholarship)
        days_remaining = DeadlineService.days_remaining(scholarship)
        urgency = DeadlineService.urgency(scholarship)

        return render(
            request, self.template_name,
            {
                "profile_missing": False,
                "scholarship": scholarship,
                "recommendation": recommendation,
                "recommendation_error": recommendation_error,
                "eligibility_results": eligibility_results,
                "overall_status": overall_status,
                "gaps": gaps,
                "readiness": readiness,
                "days_remaining": days_remaining,
                "urgency": urgency,
            },
        )
