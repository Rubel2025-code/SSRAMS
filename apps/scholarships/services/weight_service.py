"""
apps/scholarships/services/weight_service.py

Owns: ScholarshipCriterionWeight CRUD and the FR-04/FR-06 "sum to
exactly 100%" validation. This is the most important file in Prompt 3
— see apps/scholarships/models.py's module docstring for the full
design rationale on why weights are scholarship-specific.

ALL arithmetic here uses ``decimal.Decimal`` exclusively — never float
— per the project brief's explicit instruction and Prompt 1's existing
convention (Scholarship.weights_are_valid() already does this; this
service extends that, it does not replace it).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditLog
from apps.audit.services import log_event

from ..models import Scholarship, ScholarshipCriterion, ScholarshipCriterionWeight

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


class WeightValidationResult:
    """
    Plain data object returned by WeightService.validate_configuration —
    used by both the publish gate (pass/fail only matters) and the
    provider-facing weight-management UI (needs the total and the
    per-criterion breakdown to render the "TOTAL 100% ✓ / ✗" summary
    from the project brief §8).
    """

    def __init__(self, total: Decimal, is_valid: bool, issues: list[str], per_criterion: dict):
        self.total = total
        self.is_valid = is_valid
        self.issues = issues
        self.per_criterion = per_criterion  # {criterion_id: Decimal or None}


class WeightService:
    @staticmethod
    def to_decimal(raw_value) -> Decimal:
        """
        Parse user input into a Decimal, raising ValidationError (never
        a bare Python exception) on anything malformed — the one place
        weight input is converted from a string/form value, so every
        caller gets the same error behavior.
        """
        if raw_value is None or str(raw_value).strip() == "":
            raise ValidationError("Weight is required.")
        try:
            value = Decimal(str(raw_value))
        except InvalidOperation:
            raise ValidationError(f"'{raw_value}' is not a valid decimal number.")
        return value

    @staticmethod
    def set_weight(
        *, criterion: ScholarshipCriterion, weight_percent, actor,
    ) -> ScholarshipCriterionWeight:
        """
        Create or update the single weight for one criterion. Rejects:
          - negative values
          - values greater than 100 (a single criterion can never
            exceed the scholarship's entire 100%)
          - non-decimal input (via to_decimal)

        Zero is permitted here (a provider may legitimately set a
        criterion's weight to 0 while deciding whether to include it —
        WeightService.validate_configuration is what enforces the
        *sum*; a single zero-weight criterion is not itself invalid).
        """
        value = WeightService.to_decimal(weight_percent)

        if value < ZERO:
            raise ValidationError("Weight cannot be negative.")
        if value > ONE_HUNDRED:
            raise ValidationError("A single criterion's weight cannot exceed 100%.")

        weight, _created = ScholarshipCriterionWeight.objects.update_or_create(
            criterion=criterion,
            defaults={"weight_percent": value, "set_by": actor},
        )

        log_event(
            actor=actor,
            event_type=AuditLog.EventType.WEIGHT_CHANGED,
            related_object=weight,
            description=f"Weight for '{criterion.get_criterion_type_display()}' on "
            f"'{criterion.scholarship.title}' set to {value}%.",
        )
        return weight

    @staticmethod
    @transaction.atomic
    def set_weights_bulk(*, scholarship: Scholarship, weights_by_criterion_id: dict, actor) -> WeightValidationResult:
        """
        Set multiple criteria's weights in one atomic operation (the
        weight-management form in project brief §8 submits the whole
        table at once). Validates every value BEFORE writing any of
        them, so a single bad row never leaves the scholarship in a
        half-updated state.

        ``weights_by_criterion_id``: {criterion_id (int or str): raw weight value}
        Only criteria belonging to ``scholarship`` are accepted; any
        other id raises ValidationError rather than being silently
        ignored (protects against a tampered form submitting another
        provider's criterion id — ownership of the criterion is
        reverified against this specific scholarship).
        """
        criteria = {c.pk: c for c in scholarship.criteria.all()}

        parsed: dict[int, Decimal] = {}
        for raw_id, raw_value in weights_by_criterion_id.items():
            criterion_id = int(raw_id)
            if criterion_id not in criteria:
                raise ValidationError(
                    "One or more criteria do not belong to this scholarship."
                )
            value = WeightService.to_decimal(raw_value)
            if value < ZERO:
                raise ValidationError(
                    f"Weight for '{criteria[criterion_id].get_criterion_type_display()}' cannot be negative."
                )
            if value > ONE_HUNDRED:
                raise ValidationError(
                    f"Weight for '{criteria[criterion_id].get_criterion_type_display()}' cannot exceed 100%."
                )
            parsed[criterion_id] = value

        for criterion_id, value in parsed.items():
            ScholarshipCriterionWeight.objects.update_or_create(
                criterion=criteria[criterion_id],
                defaults={"weight_percent": value, "set_by": actor},
            )

        log_event(
            actor=actor,
            event_type=AuditLog.EventType.WEIGHT_CHANGED,
            related_object=scholarship,
            description=f"Weights updated for '{scholarship.title}' "
            f"({len(parsed)} criterion/criteria).",
        )

        return WeightService.validate_configuration(scholarship)

    @staticmethod
    def validate_configuration(scholarship: Scholarship) -> WeightValidationResult:
        """
        The full FR-04/FR-06 validation, returning a structured result
        rather than a bare bool (unlike Scholarship.weights_are_valid(),
        which this method is consistent with but goes further — it also
        explains WHY a configuration is invalid, for the UI feedback
        project brief §8 asks for: "Receive clear validation feedback").

        Handles every case project brief §7 lists explicitly:
          - no criteria at all
          - a criterion missing its weight entirely
          - duplicate criterion/weight rows (prevented at the DB level
            by ScholarshipCriterionWeight's OneToOneField — surfaced
            here only as informational, since the schema makes true
            duplicates impossible to create)
          - negative values (prevented at set_weight/set_weights_bulk
            time — a negative value can never reach the DB, so this
            method does not need to re-detect it, only sum what's there)
          - total below/above 100
        """
        criteria = list(scholarship.criteria.select_related("weight").all())
        issues: list[str] = []
        per_criterion: dict[int, Decimal | None] = {}
        total = ZERO

        if not criteria:
            issues.append("This scholarship has no eligibility criteria defined yet.")

        for criterion in criteria:
            if hasattr(criterion, "weight"):
                weight_value = criterion.weight.weight_percent
                per_criterion[criterion.pk] = weight_value
                total += weight_value
            else:
                per_criterion[criterion.pk] = None
                issues.append(
                    f"'{criterion.get_criterion_type_display()}' has no weight assigned."
                )

        if criteria and not issues:
            if total < ONE_HUNDRED:
                issues.append(f"Total weight is {total}%, which is below the required 100%.")
            elif total > ONE_HUNDRED:
                issues.append(f"Total weight is {total}%, which exceeds the required 100%.")

        is_valid = criteria and not issues and total == ONE_HUNDRED
        return WeightValidationResult(total=total, is_valid=bool(is_valid), issues=issues, per_criterion=per_criterion)
