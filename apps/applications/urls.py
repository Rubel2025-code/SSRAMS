"""
apps/applications/urls.py -- namespace "applications"
(mounted at /applications/).

`mine/` is UNCHANGED from Prompt 1 (URL name "my_applications" kept
identical) -- only its view class was replaced (MyApplicationsPlaceholderView
-> MyApplicationsView). Everything else below is new in Prompt 6.
"""

from django.urls import path

from . import views

app_name = "applications"

urlpatterns = [
    path("mine/", views.MyApplicationsView.as_view(), name="my_applications"),
    path("apply/<int:scholarship_pk>/", views.apply_to_scholarship, name="apply"),
    path("<int:pk>/", views.application_detail, name="detail"),
    path("<int:pk>/submit/", views.submit_application, name="submit"),

    path("provider/", views.provider_application_list, name="provider_list"),
    path("provider/<int:pk>/", views.provider_application_detail, name="provider_detail"),

    path("bookmarks/", views.MyBookmarksView.as_view(), name="my_bookmarks"),
    path("bookmarks/toggle/<int:scholarship_pk>/", views.toggle_bookmark, name="toggle_bookmark"),
]
