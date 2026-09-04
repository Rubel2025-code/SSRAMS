from django.apps import AppConfig


class RecommendationsConfig(AppConfig):
    """
    Owns: match scoring, eligibility, gaps, readiness, deadline
    prioritization, and Top Opportunities (FR-06, FR-07, FR-08, FR-14,
    FR-20, FR-21).

    This app owns the RESULT models for those computations. The
    deterministic calculation logic itself (the actual scoring/
    eligibility/readiness algorithms) is implemented as services in a
    later prompt — see README.md "Shared Service Architecture" for the
    RecommendationService / EligibilityService / ReadinessService /
    DeadlineService interfaces this app's models are designed to support.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.recommendations"
    verbose_name = "Recommendations, Eligibility & Readiness"
