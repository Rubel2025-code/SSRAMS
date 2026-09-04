"""
Django Admin registrations for apps.accounts (project brief Step 11:
"Register the foundational models with Django Admin where appropriate" —
this is a developer/admin inspection tool, NOT the SSRAMS Administrator
dashboard, which is built later in apps.dashboard).
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import ProviderProfile, ProviderVerification, StudentProfile, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "role", "is_account_active", "is_staff", "date_joined")
    list_filter = DjangoUserAdmin.list_filter + ("role", "is_account_active")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("SSRAMS Role", {"fields": ("role", "is_account_active")}),
    )


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "university", "department", "cgpa", "academic_level", "updated_at")
    search_fields = ("user__username", "user__email", "university", "department")
    list_filter = ("department", "academic_level")


@admin.register(ProviderProfile)
class ProviderProfileAdmin(admin.ModelAdmin):
    list_display = ("organization_name", "user", "verification_status", "contact_email", "updated_at")
    search_fields = ("organization_name", "user__username", "contact_email")
    list_filter = ("verification_status", "organization_type")


@admin.register(ProviderVerification)
class ProviderVerificationAdmin(admin.ModelAdmin):
    list_display = ("provider_profile", "status", "decided_by", "decided_at", "created_at")
    list_filter = ("status",)
    search_fields = ("provider_profile__organization_name",)
