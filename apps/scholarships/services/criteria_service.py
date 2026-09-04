"""
apps/scholarships/services/criteria_service.py

Owns: ScholarshipCriterion CRUD (FR-04). Ownership is enforced by the
caller always passing a Scholarship already resolved through
ScholarshipService.get_owned_scholarship_or_403 — this service does not
re-check ownership itself (same pattern as ScholarshipService.update_scholarship),
so it can also be used by administrator inspection/moderation flows.

CRITERION DEFINITION vs. STUDENT EVALUATION (project brief §5):
Everything in this file defines what a criterion IS (its type,
comparison operator, required value, mandatory/optional) — never
whether any given student satisfies it. That evaluation is
apps.recommendations.services.EligibilityService's job in Prompt 4,
which reads the rows this service writes but is never called from here.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.audit.models import AuditLog
from apps.audit.services import log_event

from ..models import Scholarship, ScholarshipCriterion

# Criterion types whose required_value must be interpretable as a plain
# number by the (future) eligibility service — used only for a basic
# format sanity check here, never to compute eligibility itself.
_NUMERIC_CRITERION_TYPES = {
    ScholarshipCriterion.CriterionType.CGPA,
    ScholarshipCriterion.CriterionType.INCOME,
}


class CriteriaService:
    @staticmethod
    def _validate_required_value(criterion_type: str, comparison: str, required_value: str) -> None:
        """
        Format-level validation only (is this a well-formed value for
        this criterion type?) — NOT eligibility evaluation. A CGPA
        criterion using GTE/LTE must have a numeric required_value; a
        DOCUMENTS/SKILLS criterion using CONTAINS is free-form text
        (comma-separated), interpreted by the eligibility service later.
        """
        if not required_value or not required_value.strip():
            raise ValidationError("Required value cannot be empty.")

        if criterion_type in _NUMERIC_CRITERION_TYPES and comparison in {
            ScholarshipCriterion.Comparison.GTE,
            ScholarshipCriterion.Comparison.LTE,
        }:
            try:
                float(required_value)
            except ValueError:
                raise ValidationError(
                    f"'{required_value}' is not a valid number for a "
                    f"{criterion_type} criterion using {comparison}."
                )

    @staticmethod
    def add_criterion(
        *, scholarship: Scholarship, actor,
        criterion_type: str, comparison: str, required_value: str,
        is_mandatory: bool = True,
    ) -> ScholarshipCriterion:
        CriteriaService._validate_required_value(criterion_type, comparison, required_value)

        criterion = ScholarshipCriterion(
            scholarship=scholarship,
            criterion_type=criterion_type,
            comparison=comparison,
            required_value=required_value,
            is_mandatory=is_mandatory,
        )
        criterion.full_clean()  # enforces the model's unique_builtin_criterion_type constraint path
        criterion.save()

        log_event(
            actor=actor,
            event_type=AuditLog.EventType.CRITERION_CREATED,
            related_object=criterion,
            description=f"Criterion '{criterion.get_criterion_type_display()}' "
            f"added to '{scholarship.title}'.",
        )
        return criterion

    @staticmethod
    def update_criterion(
        *, criterion: ScholarshipCriterion, actor,
        comparison: str, required_value: str, is_mandatory: bool,
    ) -> ScholarshipCriterion:
        CriteriaService._validate_required_value(criterion.criterion_type, comparison, required_value)

        criterion.comparison = comparison
        criterion.required_value = required_value
        criterion.is_mandatory = is_mandatory
        criterion.full_clean()
        criterion.save()

        log_event(
            actor=actor,
            event_type=AuditLog.EventType.CRITERION_UPDATED,
            related_object=criterion,
            description=f"Criterion '{criterion.get_criterion_type_display()}' "
            f"updated on '{criterion.scholarship.title}'.",
        )
        return criterion

    @staticmethod
    def delete_criterion(*, criterion: ScholarshipCriterion, actor) -> None:
        scholarship_title = criterion.scholarship.title
        criterion_label = criterion.get_criterion_type_display()
        log_event(
            actor=actor,
            event_type=AuditLog.EventType.CRITERION_DELETED,
            description=f"Criterion '{criterion_label}' removed from '{scholarship_title}'.",
        )
        # CASCADE also removes the associated ScholarshipCriterionWeight,
        # if any — deleting a criterion always deletes its weight too,
        # since a weight with no criterion is meaningless.
        criterion.delete()
