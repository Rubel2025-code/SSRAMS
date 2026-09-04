from django.contrib import admin

from .models import AIInteraction


@admin.register(AIInteraction)
class AIInteractionAdmin(admin.ModelAdmin):
    list_display = ("student", "interaction_type", "outcome", "gemini_model_name", "created_at")
    list_filter = ("interaction_type", "outcome")
    search_fields = ("student__user__username", "user_question")
    readonly_fields = ("facts_bundle_snapshot", "ai_response_text")
