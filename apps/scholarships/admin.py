from django.contrib import admin

from .models import Scholarship, ScholarshipCriterion, ScholarshipCriterionWeight


class ScholarshipCriterionWeightInline(admin.StackedInline):
    model = ScholarshipCriterionWeight
    extra = 0


class ScholarshipCriterionInline(admin.TabularInline):
    model = ScholarshipCriterion
    extra = 0
    show_change_link = True


@admin.register(Scholarship)
class ScholarshipAdmin(admin.ModelAdmin):
    list_display = ("title", "provider", "amount", "deadline", "is_published", "is_active", "weights_valid_display")
    list_filter = ("is_published", "is_active")
    search_fields = ("title", "provider__organization_name")
    inlines = [ScholarshipCriterionInline]

    @admin.display(description="Weights = 100%?", boolean=True)
    def weights_valid_display(self, obj):
        return obj.weights_are_valid()


@admin.register(ScholarshipCriterion)
class ScholarshipCriterionAdmin(admin.ModelAdmin):
    list_display = ("scholarship", "criterion_type", "comparison", "required_value", "is_mandatory")
    list_filter = ("criterion_type", "is_mandatory")
    inlines = [ScholarshipCriterionWeightInline]


@admin.register(ScholarshipCriterionWeight)
class ScholarshipCriterionWeightAdmin(admin.ModelAdmin):
    list_display = ("criterion", "weight_percent", "set_by", "updated_at")
