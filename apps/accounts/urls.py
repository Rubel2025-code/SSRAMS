"""
apps/accounts/urls.py — namespace "accounts" (mounted at /accounts/).

Prompt 2 adds profile view/edit routes (student + provider) and the
administrator provider-verification review routes. Student/provider
profile routes deliberately take NO id/username parameter — they always
operate on request.user's own profile, which is what makes "Student A
cannot view Student B's profile via URL manipulation" true by
construction rather than by an ownership check that could be forgotten
(see accounts/views.py for the one place an id *does* appear in the
URL: the admin verification-review routes, which enforce role_required
+ get_object_or_404 explicitly).
"""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.SSRAMSLoginView.as_view(), name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.RegisterChoiceView.as_view(), name="register_choice"),
    path("register/student/", views.register_student, name="register_student"),
    path("register/provider/", views.register_provider, name="register_provider"),

    # Student profile (FR-02) — always request.user's own profile.
    path("profile/student/", views.student_profile_view, name="student_profile"),
    path("profile/student/edit/", views.student_profile_edit, name="student_profile_edit"),

    # Provider profile (FR-03) — always request.user's own profile.
    path("profile/provider/", views.provider_profile_view, name="provider_profile"),
    path("profile/provider/edit/", views.provider_profile_edit, name="provider_profile_edit"),

    # Administrator provider-verification review (FR-03, FR-17).
    path("admin/verifications/", views.admin_verification_list, name="admin_verification_list"),
    path(
        "admin/verifications/<int:provider_id>/",
        views.admin_verification_detail,
        name="admin_verification_detail",
    ),
]
