from django.contrib import admin

from .models import Application, ApplicationStatusHistory, Bookmark


class ApplicationStatusHistoryInline(admin.TabularInline):
    model = ApplicationStatusHistory
    extra = 0
    readonly_fields = ("from_status", "to_status", "changed_by", "note", "created_at")


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("student", "scholarship", "status", "submitted_at", "updated_at")
    list_filter = ("status",)
    search_fields = ("student__user__username", "scholarship__title")
    inlines = [ApplicationStatusHistoryInline]


@admin.register(Bookmark)
class BookmarkAdmin(admin.ModelAdmin):
    list_display = ("student", "scholarship", "created_at")
    search_fields = ("student__user__username", "scholarship__title")
