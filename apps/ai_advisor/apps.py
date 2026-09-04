from django.apps import AppConfig


class AiAdvisorConfig(AppConfig):
    """
    Owns: the Facts Bundle builder, Gemini integration, the AI Strategy
    Planner, the Profile Improvement Advisor, and the Context-Aware AI
    Advisor (FR-10, FR-19, FR-22, FR-23).

    HARD BOUNDARY (SRS §2.1.1, FR-19, project brief Step 8): Gemini must
    never query the database directly and must never compute Match
    Score, Eligibility, Eligibility Gap, Readiness, Deadline urgency, or
    scholarship impact counts. Those are always read from
    apps.recommendations (and apps.applications) results. This app only
    assembles what Django has already computed into a Facts Bundle and
    sends *that* to Gemini — see services/facts_bundle_service.py and
    services/gemini_service.py.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ai_advisor"
    verbose_name = "AI Advisor (Gemini Guidance Layer)"
