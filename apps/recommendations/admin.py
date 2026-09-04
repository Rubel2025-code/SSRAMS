from django.contrib import admin

from .models import EligibilityResult, ReadinessResult, RecommendationResult


@admin.register(RecommendationResult)
class RecommendationResultAdmin(admin.ModelAdmin):
    list_display = ("student", "scholarship", "match_score_percent", "computed_at")
    list_filter = ("scholarship",)
    search_fields = ("student__user__username", "scholarship__title")


@admin.register(EligibilityResult)
class EligibilityResultAdmin(admin.ModelAdmin):
    list_display = ("student", "scholarship", "criterion", "status", "quantified_gap")
    list_filter = ("status",)
    search_fields = ("student__user__username", "scholarship__title")


@admin.register(ReadinessResult)
class ReadinessResultAdmin(admin.ModelAdmin):
    list_display = ("student", "scholarship", "readiness_percent", "completed_items_count", "required_items_count")
    search_fields = ("student__user__username", "scholarship__title")
