from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "actor", "related_object", "ip_address")
    list_filter = ("event_type",)
    search_fields = ("actor__username", "description")
    readonly_fields = (
        "actor", "event_type", "description", "related_content_type",
        "related_object_id", "ip_address", "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        # Audit rows are only ever created via apps.audit.services.log_event,
        # never hand-entered through the admin (FR-18 integrity).
        return False

    def has_change_permission(self, request, obj=None):
        return False
