"""
Abstract base models shared across apps, so every app's models get
consistent timestamp fields without redefining them (project brief:
"Define timestamps" for every model, "NO duplicate business logic
across apps").
"""

from django.db import models


class TimeStampedModel(models.Model):
    """Adds created_at / updated_at to any model that inherits it."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
