"""
Root URL configuration for SSRAMS v3.1.

Establishes the URL namespace foundation (project brief, Step 10). Each
app owns and documents its own urls.py; later prompts add the concrete
endpoints inside those files without touching this router.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Django's built-in admin (foundation only — see project brief Step 11.
    # This is NOT the SSRAMS Administrator dashboard; that is
    # apps.dashboard's "admin" role view, built in a later prompt).
    path("admin/", admin.site.urls),

    # App namespaces. Each entry below routes to that app's own urls.py.
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("scholarships/", include("apps.scholarships.urls", namespace="scholarships")),
    path("recommendations/", include("apps.recommendations.urls", namespace="recommendations")),
    path("applications/", include("apps.applications.urls", namespace="applications")),
    path("ai/", include("apps.ai_advisor.urls", namespace="ai_advisor")),
    path("dashboard/", include("apps.dashboard.urls", namespace="dashboard")),

    # Root "/" redirects into the dashboard app (which itself redirects
    # to /accounts/login/ for anonymous users).
    path("", include("apps.dashboard.urls_root")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
