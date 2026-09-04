"""
apps/dashboard/urls_root.py

Handles the bare "/" route only (see config/urls.py). Kept separate
from urls.py (which owns the "dashboard" namespace at /dashboard/) so
the site root isn't itself inside that namespace.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.landing, name="root"),
]
