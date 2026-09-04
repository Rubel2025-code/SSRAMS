"""
apps/scholarships/models.py

Owns: Scholarship, ScholarshipCriterion, ScholarshipCriterionWeight.

THE CENTRAL v3.1 DESIGN DECISION — SCHOLARSHIP-SPECIFIC WEIGHTS
-----------------------------------------------------------------
FR-04 / FR-06 / SRS §2.4 are explicit: there is NO platform-wide global
weighting formula. Each scholarship defines its own set of criteria and
its own weight per criterion, and those weights must sum to 100% before
the scholarship can be published or scored.

That rules out a naive design where "weight" is a fixed field per
criterion *type* (e.g. a single global "CGPA weight = 40%" setting) —
two different scholarships must be able to give CGPA two different
weights simultaneously. The model below represents this as:

    Scholarship "1..N" ScholarshipCriterion "1..1" ScholarshipCriterionWeight

i.e. a ScholarshipCriterion already belongs to exactly one Scholarship
(so "CGPA for Scholarship A" and "CGPA for Scholarship B" are two
separate rows, each free to carry its own weight), and its weight is
modeled as its own row (ScholarshipCriterionWeight) rather than a bare
field on ScholarshipCriterion. This split exists so that:

  1. Weight changes are individually auditable (FR-18 lists "weight
     changes" as an audit-logged event) without diffing the whole
     criterion (which also holds the threshold/requirement value).
  2. Exactly one weight is ever "active" per criterion at a time
     (enforced by a OneToOneField), while still leaving room for a
     provider to revise a weight — old rows are simply replaced, and
     the FK from AuditLog can point at the specific weight record that
     changed.

The "sum to 100%" rule (FR-04, FR-06) is enforced at the service layer
in a later prompt (apps.scholarships will expose a
``validate_weights_complete(scholarship)`` check consumed by both the
publish action and apps.recommendations before scoring) — see
Scholarship.weights_are_valid() below for the read-only check available
now, which later services build on rather than duplicate.

LIFECYCLE (Prompt 3) — WHY THERE IS NO STATUS ENUM
-----------------------------------------------------------------
The SRS never names a multi-state Scholarship lifecycle (no "Draft" /
"Submitted" / "Closed" status is specified anywhere in FR-04 or
elsewhere) — the only stateful language is FR-04's "shall not allow a
scholarship to go live... until its weights sum to 100%", i.e. a single
published/not-published gate. The *Application* model (apps.applications)
has its own explicit FR-09 status lifecycle (Draft -> Submitted ->
Under Review -> Shortlisted -> Approved/Rejected) — that is a different
model for a different concept and is not duplicated or reused here.

Scholarship therefore represents state with three independent booleans/
properties rather than one invented status enum:
  - is_published: the FR-04 publish gate (set only via PublicationService,
    only when can_be_published() is True).
  - is_active: a moderation/withdrawal flag an administrator or the
    owning provider can clear without deleting history (FR-17
    moderation, FR-18 "closure/deactivation" audit event) — independent
    of is_published so "unpublish" and "deactivate" remain distinguishable
    if a later prompt needs that distinction.
  - is_expired (derived property, not a DB column): True once
    ``timezone.now() >= deadline``. Not stored because it is a pure
    function of `deadline` that would otherwise need a scheduled job to
    keep in sync — computing it at read time is both simpler and always
    correct.

``is_visible_to_students`` combines all three into the single check
every discovery/detail view should use, and ``can_be_published`` adds
the FR-03 verified-provider gate on top of ``weights_are_valid`` for
the one place (PublicationService) that decides whether is_published
may become True.
"""

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.accounts.models import ProviderProfile
from apps.common.models import TimeStampedModel


class Scholarship(TimeStampedModel):
    """A scholarship published (or being drafted) by a Provider (FR-04)."""

    provider = models.ForeignKey(
        ProviderProfile,
        on_delete=models.CASCADE,
        related_name="scholarships",
    )

    title = models.CharField(max_length=255)
    description = models.TextField()
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, help_text="Award amount in BDT."
    )
    deadline = models.DateTimeField()

    # FR-04 lists "required documents" and application
    # information/instructions as scholarship-level descriptive fields
    # (distinct from an eligibility CRITERION of type DOCUMENTS on
    # ScholarshipCriterion, which governs scoring/eligibility — see that
    # model's docstring). required_documents is a JSONField list of
    # document names, following the same pattern already established by
    # StudentProfile.skills/interests/etc. (apps/accounts/models.py) so
    # no new field-storage convention is introduced.
    required_documents = models.JSONField(
        default=list, blank=True,
        help_text='Document names a student must submit, e.g. '
        '["Recommendation Letter", "Income Certificate"]. Descriptive '
        "only — an eligibility rule tied to a document goes on "
        "ScholarshipCriterion (criterion_type=DOCUMENTS) instead.",
    )
    application_instructions = models.TextField(
        blank=True,
        help_text="Free-text guidance for applicants, e.g. how/where to submit.",
    )

    # FR-04/FR-18: administrator or provider can withdraw a scholarship
    # from visibility/scoring without deleting its history (moderation,
    # or a provider closing it early). Distinct from "expired", which is
    # derived from `deadline` at read time rather than stored — see
    # is_expired below. The SRS does not name a multi-state lifecycle
    # for Scholarship (only "go live"/not, FR-04), so this stays a
    # single boolean rather than a new status enum; see models.py
    # module docstring "Lifecycle" section below for the full reasoning.
    is_active = models.BooleanField(
        default=True,
        help_text="False if withdrawn by the provider or deactivated by an "
        "administrator (moderation). Independent of is_published and of "
        "deadline-based expiry — see is_expired.",
    )

    # FR-04: "shall not allow a scholarship to go live ... until its
    # criteria weights are set and sum to 100%". is_published is the
    # explicit gate a later prompt's publish action flips only after
    # weights_are_valid() is True; it defaults to False so a Scholarship
    # can exist in-progress (criteria being added) without being visible
    # to students or eligible for scoring (FR-06).
    is_published = models.BooleanField(
        default=False,
        help_text="Only True once criteria weights sum to 100% (FR-04). "
        "Unpublished scholarships never enter recommendation scoring (FR-06) "
        "or student-facing search (FR-05).",
    )

    class Meta:
        # "-id": deterministic tie-break (see apps.audit.models.AuditLog.Meta).
        # Needed here because the student catalogue is paginated — with
        # equal created_at values, an unstable tie could show the same
        # scholarship on two pages, or hide one entirely.
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["is_published"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["deadline"]),
        ]

    def __str__(self):
        return self.title

    @property
    def is_expired(self) -> bool:
        """
        Derived, not stored: a scholarship past its deadline is treated
        as closed for discovery/scoring purposes without a separate
        stored "Closed" status the SRS never names. See
        ``ScholarshipQuerySet``-equivalent filters in
        apps.scholarships.services for how list/catalog views use this.
        """
        from django.utils import timezone

        return timezone.now() >= self.deadline

    @property
    def is_visible_to_students(self) -> bool:
        """Single source of truth for 'should a student ever see this
        scholarship in discovery' — published, active, and not expired."""
        return self.is_published and self.is_active and not self.is_expired

    @property
    def total_weight_percent(self) -> Decimal:
        """Sum of all criteria weights for this scholarship (not related
        to the Scholarship.is_active moderation flag — every criterion
        with an assigned ScholarshipCriterionWeight is included)."""
        total = Decimal("0")
        for criterion in self.criteria.select_related("weight").all():
            if hasattr(criterion, "weight"):
                total += criterion.weight.weight_percent
        return total

    def weights_are_valid(self) -> bool:
        """
        Read-only check mirroring FR-04's publish/scoring gate: every
        criterion must carry a weight, and the weights must sum to
        exactly 100%. The full ``validate_weights_complete()`` service
        (added with apps.recommendations in a later prompt) calls this
        rather than re-implementing it, so the rule is defined once.
        """
        criteria = list(self.criteria.select_related("weight").all())
        if not criteria:
            return False
        if any(not hasattr(c, "weight") for c in criteria):
            return False
        return self.total_weight_percent == Decimal("100")

    def can_be_published(self) -> bool:
        """
        Full FR-04 publish gate: weights valid AND the owning provider is
        admin-verified (FR-03: "only admin-approved providers may publish
        scholarships"). Reads self.provider.verification_status via the
        FK that already exists on this model — not a new cross-app
        coupling. apps.scholarships.services.PublicationService is the
        one place that calls this before flipping is_published, so the
        rule is defined once and enforced the same way everywhere.
        """
        from apps.common.enums import ProviderVerificationStatus

        return (
            self.weights_are_valid()
            and self.provider.verification_status == ProviderVerificationStatus.APPROVED
        )


class ScholarshipCriterion(TimeStampedModel):
    """
    A single eligibility/scoring criterion belonging to one scholarship
    (FR-04: "eligibility criteria (CGPA, department, academic level,
    income, location, skills, required documents, other criteria)").

    ``criterion_type`` distinguishes the built-in criteria the SRS names
    explicitly from a provider-defined custom criterion ("other
    criteria"). ``comparison`` + ``required_value`` describe the
    eligibility rule itself (FR-07 reads these to classify Eligible /
    Not Eligible / Missing Information and compute a quantified gap);
    the weight used for *scoring* the same criterion lives on the
    related ScholarshipCriterionWeight, not here — see module docstring.
    """

    class CriterionType(models.TextChoices):
        CGPA = "cgpa", "CGPA"
        DEPARTMENT = "department", "Department"
        ACADEMIC_LEVEL = "academic_level", "Academic Level"
        INCOME = "income", "Family Income"
        LOCATION = "location", "Location"
        SKILLS = "skills", "Skills"
        DOCUMENTS = "documents", "Required Documents"
        OTHER = "other", "Other"

    class Comparison(models.TextChoices):
        """How ``required_value`` should be compared against the student's
        value. Only meaningful for numeric criterion types (CGPA, INCOME);
        non-numeric types (DEPARTMENT, SKILLS, ...) use EQUALS/CONTAINS-style
        matching implemented by the eligibility service in a later prompt."""

        GTE = "gte", "Greater than or equal to"
        LTE = "lte", "Less than or equal to"
        EQUALS = "equals", "Equals"
        CONTAINS = "contains", "Contains / Includes"

    scholarship = models.ForeignKey(
        Scholarship, on_delete=models.CASCADE, related_name="criteria"
    )
    criterion_type = models.CharField(max_length=20, choices=CriterionType.choices)
    comparison = models.CharField(max_length=10, choices=Comparison.choices)

    # Free-form so it can hold "3.50", "CSE", "30000.00", or a JSON-encoded
    # list of required skills/documents depending on criterion_type; the
    # eligibility service (later prompt) is responsible for interpreting
    # it according to criterion_type + comparison.
    required_value = models.CharField(max_length=255)

    is_mandatory = models.BooleanField(
        default=True,
        help_text="If False, failing this criterion contributes to "
        "'near-eligible' classification (FR-20) rather than hard "
        "ineligibility. Interpreted by the eligibility service.",
    )

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["scholarship", "criterion_type"],
                condition=models.Q(criterion_type__in=[
                    "cgpa", "department", "academic_level", "income", "location",
                ]),
                name="unique_builtin_criterion_type_per_scholarship",
            )
        ]
        indexes = [
            models.Index(fields=["criterion_type"]),
        ]

    def __str__(self):
        return f"{self.scholarship.title} — {self.get_criterion_type_display()}"


class ScholarshipCriterionWeight(TimeStampedModel):
    """
    The scholarship-specific weight for exactly one criterion (FR-04,
    FR-06). See the module docstring for why this is its own model
    rather than a field on ScholarshipCriterion.
    """

    criterion = models.OneToOneField(
        ScholarshipCriterion,
        on_delete=models.CASCADE,
        related_name="weight",
    )
    weight_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="This criterion's share of the scholarship's Match Score, "
        "e.g. 40.00 for 40%. All weights for one scholarship must sum to "
        "exactly 100% before scoring/publishing (FR-04, FR-06).",
    )
    set_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Provider user who last set this weight (for audit trail).",
    )

    class Meta:
        indexes = [
            models.Index(fields=["weight_percent"]),
        ]

    def __str__(self):
        return f"{self.criterion} = {self.weight_percent}%"
