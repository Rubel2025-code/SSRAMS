"""
apps/scholarships/urls.py -- namespace "scholarships" (mounted at /scholarships/).

Prompt 1 defined only `list`. That name is preserved and now points at
ScholarshipListView, which branches by role internally (see views.py)
so every existing template link (navbar, dashboards) keeps working
unchanged. Everything else below is new in Prompt 3.
"""

from django.urls import path

from . import views

app_name = "scholarships"

urlpatterns = [
    path("", views.ScholarshipListView.as_view(), name="list"),
    path("<int:pk>/", views.ScholarshipDetailView.as_view(), name="detail"),

    # --- Provider management (FR-04) ---
    path("provider/create/", views.provider_scholarship_create, name="provider_create"),
    path("provider/<int:pk>/", views.provider_scholarship_detail, name="provider_detail"),
    path("provider/<int:pk>/edit/", views.provider_scholarship_edit, name="provider_edit"),
    path("provider/<int:pk>/delete/", views.provider_scholarship_delete, name="provider_delete"),
    path("provider/<int:pk>/publish/", views.provider_scholarship_publish, name="provider_publish"),
    path("provider/<int:pk>/unpublish/", views.provider_scholarship_unpublish, name="provider_unpublish"),
    path("provider/<int:pk>/deactivate/", views.provider_scholarship_deactivate, name="provider_deactivate"),

    # --- Criteria management ---
    path("provider/<int:pk>/criteria/", views.provider_criteria_manage, name="provider_criteria"),
    path(
        "provider/<int:pk>/criteria/<int:criterion_id>/edit/",
        views.provider_criterion_edit, name="provider_criterion_edit",
    ),
    path(
        "provider/<int:pk>/criteria/<int:criterion_id>/delete/",
        views.provider_criterion_delete, name="provider_criterion_delete",
    ),

    # --- Weight management (FR-04, FR-06) ---
    path("provider/<int:pk>/weights/", views.provider_weights_manage, name="provider_weights"),

    # --- Administrator inspection/moderation ---
    path("admin/<int:pk>/", views.admin_scholarship_detail, name="admin_detail"),
    path("admin/<int:pk>/moderate/", views.admin_scholarship_moderate, name="admin_moderate"),
]
