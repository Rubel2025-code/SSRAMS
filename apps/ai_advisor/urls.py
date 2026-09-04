"""
apps/ai_advisor/urls.py -- namespace "ai_advisor" (mounted at /ai/).

`advisor/` is UNCHANGED from Prompt 1 (URL name "advisor" kept
identical) -- only its view class was replaced
(AIAdvisorPlaceholderView -> AIAdvisorView). `strategy/` and
`improve/` are new in Prompt 7.
"""

from django.urls import path

from . import views

app_name = "ai_advisor"

urlpatterns = [
    path("advisor/", views.AIAdvisorView.as_view(), name="advisor"),
    path("strategy/", views.StrategyPlannerView.as_view(), name="strategy_planner"),
    path("improve/", views.ProfileImprovementView.as_view(), name="profile_improvement"),
]
