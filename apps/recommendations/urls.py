"""
apps/recommendations/urls.py -- namespace "recommendations"
(mounted at /recommendations/).

`top-opportunities/` now points at a real implementation
(TopOpportunitiesView, Prompt 5) -- the URL NAME is unchanged from
Prompt 1 (still "top_opportunities"), so every existing link (navbar,
dashboards) keeps working with no template changes needed at the call
site. `my/` and `<int:pk>/` (Prompt 4) are extended in Prompt 5 with
readiness + deadline data, with no URL changes. No new URL name takes
a student id -- every view always operates on request.user's own
StudentProfile (see views.py "SS19 security" notes), the same
ownership-by-construction pattern apps.accounts established in
Prompt 2.
"""

from django.urls import path

from . import views

app_name = "recommendations"

urlpatterns = [
    path("top-opportunities/", views.TopOpportunitiesView.as_view(), name="top_opportunities"),
    path("my/", views.MyRecommendationsView.as_view(), name="my_recommendations"),
    path("<int:pk>/", views.RecommendationDetailView.as_view(), name="detail"),
]
