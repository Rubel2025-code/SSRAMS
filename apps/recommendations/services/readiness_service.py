"""
apps/recommendations/services/readiness_service.py

Prompt 1 established this interface (signature below is UNCHANGED from
that stub). Prompt 5 fully implements it.

--------------------------------------------------------------------
READINESS FORMULA -- documented per SS3 (Prompt 5 brief), FR-08
--------------------------------------------------------------------
FR-08 (SRS): "The system shall calculate Application Readiness as
completed items divided by required items (e.g. 75% Ready), list which
items are complete and which are missing." The SRS's own worked example
(SS4.3) is "Required items = 5, completed = 4 -> Readiness = 4/5 = 80%."

READINESS IS NOT MATCH SCORE (SS13 of the Prompt 5 brief; also FR-08 vs
FR-06 are two separate SRS requirements with two separate models --
RecommendationResult vs ReadinessResult, Prompt 1). This service never
reads or derives from RecommendationResult.match_score_percent.

WHAT COUNTS AS A "REQUIRED ITEM" (per what the current implementation
actually supports -- SS2 "Only use inputs actually supported by the
SRS/current implementation"):
  1. Every MANDATORY ScholarshipCriterion on the scholarship (FR-04)
     -- e.g. "CGPA >= 3.50", "Department = CSE". Non-mandatory
     (is_mandatory=False) criteria are NOT counted as required items,
     consistent with how EligibilityService already treats them as
     non-blocking for overall eligibility (Prompt 4).
  2. Every entry in Scholarship.required_documents (FR-04's descriptive
     document list, Prompt 3) -- e.g. "Recommendation Letter".

A mandatory-criterion item is COMPLETE if and only if
EligibilityService's own authoritative EligibilityResult for that
criterion is ELIGIBLE (Prompt 4's result, read here, never
recalculated -- SS "Do NOT recalculate Match Score or Eligibility in
Prompt 5"). A NOT_ELIGIBLE or MISSING_INFO criterion is incomplete.

A required-document item is ALWAYS counted as incomplete at this stage.
This is a deliberate, documented honesty choice, not an oversight: no
document-submission mechanism exists yet anywhere in the codebase
(apps.applications.models.Application.submitted_data is an empty
JSONField with no upload UI -- "built in a later prompt", and this
prompt explicitly forbids implementing the application workflow, SS/
"Do NOT implement the application workflow yet"). Marking a document
"complete" without any evidence it was ever submitted would be
fabricated information (project brief SS17 "Do not display misleading
information"). Once a later prompt adds real document submission
tracking, this is the one place that check needs to change.

If a scholarship has ZERO required items (no mandatory criteria and no
required_documents), readiness is defined as 100% -- there is nothing
outstanding, so "fully ready" is the honest characterization (an empty
checklist is a completed checklist), matching the same "vacuously true"
convention used elsewhere in this codebase's boolean logic.

--------------------------------------------------------------------
PRECISION / ROUNDING POLICY -- identical to Prompt 4's, not reinvented
--------------------------------------------------------------------
completed_items_count / required_items_count computed as Decimal, times
100, rounded ONCE via ROUND_HALF_UP to 2 decimal places (matching
ReadinessResult.readiness_percent's decimal_places=2 field and
RecommendationService.compute_match_score's exact same policy from
Prompt 4 -- see that module's docstring for why ROUND_HALF_UP was
chosen explicitly over Decimal's default ROUND_HALF_EVEN).

--------------------------------------------------------------------
ACTION PLAN -- factual only, no AI, no promises (SS6)
--------------------------------------------------------------------
Each missing item produces exactly one factual action sentence:
  - Missing mandatory criterion: reuses the EXACT explanation text
    EligibilityService/GapService already produced for that criterion
    (Prompt 4's GapEntry.explanation) -- never a second, independently
    worded description of the same fact.
  - Missing required document: a fixed, factual sentence naming the
    document ("Submit the required document: X."). No promises like
    "this will take 3 days" or "prioritize this first" are invented --
    ordering is deterministic (criteria first in scholarship criteria
    order, then documents in required_documents order) rather than a
    guessed urgency ranking, since the SRS's own example ordering
    ("request the recommendation letter first — longer lead time") is
    domain knowledge this system has no factual basis to assert for an
    arbitrary provider-defined document list.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from apps.accounts.models import StudentProfile
from apps.common.enums import EligibilityStatus
from apps.recommendations.models import ReadinessResult
from apps.scholarships.models import Scholarship

from .eligibility_service import EligibilityService

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
TWO_PLACES = Decimal("0.01")


class ReadinessService:
    """Deterministic readiness % + ordered action plan computation (FR-08)."""

    @staticmethod
    def compute_readiness(student: StudentProfile, scholarship: Scholarship) -> ReadinessResult:
        """
        Compute completed/required application items, the readiness
        percentage, and an ordered action plan for this student/
        scholarship pair (FR-08). See module docstring for the exact
        formula and what counts as a "required item."

        Reuses EligibilityService.evaluate() (Prompt 4's authoritative
        per-criterion result) rather than re-evaluating criteria itself
        -- this is the one and only place this service touches
        eligibility data, and it is a read, not a recomputation.
        """
        eligibility_results = EligibilityService.evaluate(student, scholarship)
        mandatory_results = [r for r in eligibility_results if r.criterion.is_mandatory]

        required_documents = scholarship.required_documents or []

        required_items_count = len(mandatory_results) + len(required_documents)

        missing_items = []
        action_plan = []

        for result in mandatory_results:
            if result.status != EligibilityStatus.ELIGIBLE:
                label = result.criterion.get_criterion_type_display()
                missing_items.append(label)
                # Reuse the exact explanation EligibilityService already
                # computed (Prompt 4) -- never a second independent
                # description of the same fact (SS "Use Prompt 4's
                # Eligibility Gap output as an authoritative input").
                action_plan.append(result.explanation or f"Address the unmet requirement: {label}.")

        for document_name in required_documents:
            missing_items.append(document_name)
            action_plan.append(f"Submit the required document: {document_name}.")

        completed_items_count = required_items_count - len(missing_items)

        if required_items_count == 0:
            # No required items at all -- vacuously fully ready (see
            # module docstring). Avoids a ZeroDivisionError while
            # remaining an honest, documented characterization rather
            # than an arbitrary fallback value.
            readiness_percent = ONE_HUNDRED
        else:
            raw_fraction = Decimal(completed_items_count) / Decimal(required_items_count)
            readiness_percent = (raw_fraction * ONE_HUNDRED).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        result, _created = ReadinessResult.objects.update_or_create(
            student=student, scholarship=scholarship,
            defaults={
                "required_items_count": required_items_count,
                "completed_items_count": completed_items_count,
                "readiness_percent": readiness_percent,
                "missing_items": missing_items,
                "action_plan": action_plan,
            },
        )
        return result
