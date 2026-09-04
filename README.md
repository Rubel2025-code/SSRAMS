# SSRAMS v3.1 — Foundation

Smart Scholarship Recommendation & Application Management System with
AI-Powered Scholarship Advisor. IUBAT — August 2026.

This is the **project foundation**: the Django scaffold, data model,
URL routing, auth/RBAC system, and service-layer interfaces that every
feature in SSRAMS SRS v3.1 is built on top of. It intentionally does
**not** implement the recommendation/eligibility/readiness algorithms
or the Gemini prompting logic — those are feature work for later
prompts, each with a `NotImplementedError` stub and a docstring pointing
at exactly which requirement it fulfills, so the team can divide that
work without renegotiating the architecture.

---

## 1. Known Limitations (read this first)

Prompts 1–7 were originally *written* in a sandboxed environment with no
PyPI access and no database, so nothing could be executed there. **That
is no longer the case: the project has since been run against a real
Django install and the full test suite passes.** As of 2026-08-31, on
Python 3.11.3 / Django 5.2.3:

- `python manage.py check` → **System check identified no issues (0 silenced).**
- `python manage.py makemigrations` → **12 migration files now exist and
  are committed** (`0001_initial` + one `0002_*` per app with model
  `Meta` changes, across `accounts`, `ai_advisor`, `applications`,
  `audit`, `recommendations`, `scholarships`).
- `python manage.py makemigrations --check --dry-run` → **No changes
  detected** (models and migrations are in sync — no drift).
- `python manage.py test apps` → **Ran 296 tests … OK** (zero failures,
  zero errors).

See §11 for the full verification record, including the bugs that first
execution actually caught and how they were fixed.

Two limitations remain:

- **The suite above was run on the `sqlite_dev_fallback` engine, not on
  SQL Server.** SQL Server remains the configured and intended database
  (`DB_ENGINE=mssql` by default in `.env.example`, matching SRS §2.1.3);
  the fallback exists in `config/settings.py` purely so a machine without
  SQL Server + ODBC Driver 18 set up yet can still `migrate`/`runserver`/
  `test` locally. It is explicitly commented as temporary and is not what
  the team should develop against long-term. **The migrations have not yet
  been applied against a real SQL Server instance**, so backend-specific
  issues (collation, `NVARCHAR` sizing, `DecimalField` precision) are
  still unverified. See §4 for exactly what to install.
- **The Gemini integration has never made a real network call.** Every AI
  test mocks the SDK boundary deliberately (see §2g and the
  `GeminiServiceGrounding` tests), and the suite above ran with
  `GEMINI_API_KEY` empty, exercising the "AI unavailable, deterministic
  features still work" path per SRS §2.5. Live prompt/response quality
  against the real API is untested. (Superseded by §11b — the live
  request path has since been exercised end-to-end.)

One known piece of technical debt, found during that first run: the
pre-1.0 Google generative client **is deprecated upstream** and prints
"All support … has ended … please switch to the `google.genai` package"
on import. It should be migrated before production. The change
is contained: Prompt 7 confined every SDK call to
`apps/ai_advisor/services/gemini_service.py`, so only that module and
the `ai_advisor` tests' patch targets need rewriting. (Resolved — see
§11b. The project is now on the current `google-genai` SDK, the legacy
client is uninstalled, and a guard test prevents the two from being
mixed again.)

---

## 2. What's in Prompt 1 vs. what's not

**Implemented and testable now:**
- Full Django project skeleton (`config/`), settings for SQL Server +
  env-based secrets, security defaults.
- Complete data model for every app (see §5) — every field, FK,
  constraint, and index described in the brief.
- Custom `User` model with `role` (Student / Provider / Admin),
  `StudentProfile`, `ProviderProfile`, `ProviderVerification`.
- URL routing skeleton with one namespace per app, wired end-to-end
  from `config/urls.py` down to a real (placeholder) view + template.
- RBAC foundation: `role_required`, `verified_provider_required`,
  `RoleRequiredMixin`, `OwnerRequiredMixin` in `apps/common/rbac.py`.
- Login / logout / role-picking registration (student + provider) —
  full working auth, not a placeholder.
- Role-aware dashboard with real (if simple) counts per role.
- `apps.audit.services.log_event()` — fully implemented, not stubbed,
  since every other app needs it available immediately.
- Base template set (Bootstrap 5 via CDN) with role-aware nav.
- Test suite covering models, relationships, constraints, and auth
  behavior for every app (see §3).

**Deliberately left as interfaces (`NotImplementedError` + docstring
pointing at the owning FR), for later prompts:**
- `RecommendationService.compute_match_score` — FR-06
- `EligibilityService.evaluate` — FR-07
- `ReadinessService.compute_readiness` — FR-08
- `DeadlineService.weekly_priority_list` / `top_opportunities` — FR-14, FR-20
- `ApplicationService.submit` / `transition_status` — FR-09
- `FactsBundleService.*` — SRS §2.1.1 (Facts Bundle assembly)
- `GeminiService.generate_guidance` — FR-19/FR-22 Gemini call itself
- `AIAdvisorService.ask_advisor` / `generate_strategy` /
  `generate_profile_improvement` — FR-10, FR-22, FR-23

Every one of these lives in a `services/` module inside the app that
owns it, with a docstring explaining exactly what it must do and which
existing model it reads/writes. Views must call these services, not
implement calculation logic inline (see §6).

---

## 2b. What Prompt 2 Added

Prompt 2 completes the authentication/profile/RBAC layer Prompt 1
scaffolded — still no recommendation, eligibility, or scholarship
business logic (that stays out until the apps that own it are built).

**Registration now creates a full, usable profile, not just an account:**
- `StudentRegistrationForm` collects university/department/CGPA/academic
  level/income/location and creates the `StudentProfile` in the same
  DB transaction as the `User` (`@transaction.atomic` — see
  `apps/accounts/forms.py`). A student who finishes registration has a
  complete profile immediately, not an empty shell to fill in later.
- `ProviderRegistrationForm` collects organization details and an
  optional verification-evidence note, and creates both the
  `ProviderProfile` (status `PENDING`) and the first
  `ProviderVerification` history row in the same transaction.

**Profile view/edit pages** (`/accounts/profile/student/`,
`/accounts/profile/provider/`, `+/edit/`) — both are `@role_required`-gated
and take **no ID parameter in the URL**: they always operate on
`request.user`'s own profile. This is deliberate — it makes "Student A
cannot view or edit Student B's profile" true by construction rather
than by an ownership check that a later change could accidentally
remove. `apps/common/rbac.OwnerRequiredMixin` remains available for the
one place an ID *does* appear in a URL (see next point) and for later
prompts' object-level views (e.g. a provider reviewing one specific
application).

**Administrator provider-verification workflow**
(`/accounts/admin/verifications/`, `+/<id>/`) — the one place in this
prompt where an object ID does appear in the URL, so access is enforced
explicitly: `@role_required(RoleChoices.ADMIN)` plus `get_object_or_404`.
Approving/rejecting updates both the specific `ProviderVerification`
submission (full history) and the denormalized `ProviderProfile.
verification_status` field the rest of the app reads — no new statuses
were introduced; it's still just `PENDING` / `APPROVED` / `REJECTED`
from `apps/common/enums.py`.

**Audit logging is now wired in**, using the `log_event()` helper that
already existed from Prompt 1 (`apps/audit/services.py` — not
reimplemented). Login, logout, registration, profile updates, and
verification decisions each call it. Two additive `AuditLog.EventType`
choices were added (`ACCOUNT_REGISTERED`, `PROFILE_UPDATED`) alongside
the existing ones — nothing renamed or removed.

**Dashboards** gained real navigation into the new pages (profile
links for students/providers; a pending-verification count + review
link for admins, computed the same way the existing dashboard counts
already were — a filtered `.count()`, no new business logic).

**Test coverage added** in `apps/accounts/tests.py` (all in addition to
Prompt 1's existing test classes, none removed): registration-creates-profile,
validation rejection (bad CGPA, negative income, missing required
field, duplicate username), profile view/edit, cross-role RBAC denial
(student/provider/admin each blocked from the other two roles' views),
ownership isolation (Student A's request never returns Student B's
data — asserted via `assertNotContains`, not just "didn't crash"), the
full verification approve/reject workflow, `verified_provider_required`
behavior before and after approval, and that every new action produces
the expected `AuditLog` row.

**Files touched this prompt:** `apps/audit/models.py` (2 new enum
choices, additive), `apps/accounts/forms.py`, `apps/accounts/views.py`,
`apps/accounts/urls.py`, `apps/accounts/tests.py`, `apps/dashboard/views.py`
(admin context only), 6 new templates under `templates/accounts/`,
`templates/base/navbar.html`, `templates/dashboard/student_dashboard.html`,
`templates/dashboard/provider_dashboard.html`, `templates/dashboard/admin_dashboard.html`.
No models, no migrations, no URL namespaces, and no existing Prompt 1
routes/behavior were changed or removed.

---

## 2c. What Prompt 3 Added

Prompt 3 builds the full scholarship management module on top of
Prompts 1–2. Still no Match Score / eligibility / gap logic anywhere
in this app — that's Prompt 4's job in `apps.recommendations`.

**Two new `Scholarship` fields, additive only:** `required_documents`
(JSONField list) and `application_instructions` (TextField) — FR-04
descriptive fields distinct from an eligibility *criterion* of type
`DOCUMENTS`. No existing field was renamed, removed, or restructured.

**No new status enum was introduced.** The SRS never names a
multi-state Scholarship lifecycle (only FR-04's single "go live" gate)
— see the extensive "LIFECYCLE" section in `apps/scholarships/models.py`'s
module docstring for the full reasoning. Instead, state is three
independent, purpose-built pieces:
- `is_published` (existing, from Prompt 1) — the FR-04 publish gate.
- `is_active` (new) — a moderation/withdrawal flag, independent of
  publish state.
- `is_expired` (new, a derived `@property`, not a database column) —
  computed from `deadline` at read time rather than stored.

`Scholarship.is_visible_to_students` combines all three into the one
check every student-facing view uses; `Scholarship.can_be_published()`
adds the FR-03 verified-provider requirement on top of the existing
`weights_are_valid()`.

**Four new services** in `apps/scholarships/services/` (all views call
these — none of this logic lives inline in views.py):
- `ScholarshipService` — CRUD plus the **one ownership choke point**,
  `get_owned_scholarship_or_403`, that every provider-facing
  scholarship/criteria/weight view uses instead of a bare
  `get_object_or_404`. Also enforces "a published scholarship cannot be
  deleted, only deactivated."
- `CriteriaService` — criterion CRUD with format-level validation only
  (is this a well-formed value for this criterion type?) — it never
  evaluates whether any student satisfies a criterion. That line is
  deliberate: eligibility *evaluation* stays out of this app entirely,
  reserved for `apps.recommendations.services.EligibilityService` in
  Prompt 4.
- `WeightService` — the FR-04/FR-06 "sum to exactly 100%" validation,
  using `decimal.Decimal` exclusively (never `float`) for every
  comparison, matching the convention `Scholarship.weights_are_valid()`
  already established in Prompt 1. Returns a structured
  `WeightValidationResult` (total, pass/fail, and a list of specific
  issues) so the UI can explain *why* a configuration is invalid, not
  just that it is. `set_weights_bulk` is wrapped in
  `@transaction.atomic` — a single bad value in a multi-row submission
  saves nothing rather than leaving a half-updated scholarship.
- `PublicationService` — the only place `Scholarship.is_published` is
  ever set to `True`; combines `WeightService`'s validation with the
  FR-03 verified-provider check so that gate can never be bypassed via
  a shortcut elsewhere.

**Views** (`apps/scholarships/views.py`, fully replacing Prompt 1's
single placeholder): the existing `scholarships:list` URL name is
preserved and now branches by `request.user.role` internally — students
get the published-scholarship catalog (FR-05, search by title/organization,
paginated, expired/unpublished/inactive scholarships excluded), providers
get their own management list, admins get a moderation list. This means
every existing link to `scholarships:list` (navbar, dashboards) kept
working with no template changes needed at the call site. New routes:
provider create/edit/delete/publish/unpublish/deactivate, criteria
add/edit/delete, weight management, student detail, and admin
inspect/moderate.

**Administrator moderation is intentionally broader than the provider
ownership model** — admin views use `role_required(ADMIN)` +
`get_object_or_404` (any scholarship), never
`get_owned_scholarship_or_403` (which would incorrectly restrict an
admin to scholarships they "own"). Moderation actions are recorded
under a distinct `SCHOLARSHIP_MODERATED` audit event so they're always
distinguishable from a provider managing their own listing.

**Audit logging**: 6 new additive `AuditLog.EventType` choices
(`SCHOLARSHIP_UNPUBLISHED`, `SCHOLARSHIP_CLOSED`, `SCHOLARSHIP_MODERATED`,
`CRITERION_CREATED`, `CRITERION_UPDATED`, `CRITERION_DELETED`) alongside
the ones already added in Prompts 1–2 — nothing renamed or removed.
Every create/update/delete/publish/unpublish/deactivate/moderate action
logs an event via the existing `log_event()` helper.

**Test coverage added** in `apps/scholarships/tests.py` (in addition to
Prompt 1's existing model tests, none removed): the new lifecycle
properties, exhaustive `WeightService` validation (negative, over-100,
non-decimal, zero-is-allowed-alone, below/above/exactly 100, a
Decimal-precision case that would fail under float rounding, atomic
bulk-set rollback, and cross-scholarship criterion-id rejection),
`PublicationService`'s full gate (weights-only failure,
verification-only failure, success case), ownership enforcement at
both the service layer and over HTTP (a second provider gets 403 on
every provider-only route for a scholarship they don't own; a student
gets 403 on every provider-only route), student discovery correctness
(published+active+non-expired only, verified via `assertNotContains`
for each excluded case), admin moderation (success, and that a
non-admin provider is denied on the admin route even for their own
scholarship), and audit-record creation for scholarship/criterion/weight
actions performed through the actual views.

**Files touched this prompt:** `apps/scholarships/models.py` (2 new
fields, several new properties/methods — additive), `apps/audit/models.py`
(6 new enum choices, additive), `apps/scholarships/admin.py`,
`apps/scholarships/tests.py` (extended), plus newly created
`apps/scholarships/forms.py`, `apps/scholarships/services/` (5 files),
`apps/scholarships/views.py`, `apps/scholarships/urls.py`, 12 templates
under `templates/scholarships/` (replacing the 1 Prompt 1 placeholder),
and small additive edits to `templates/dashboard/provider_dashboard.html`
and `templates/dashboard/admin_dashboard.html` (updated stale "later
prompt" text, added a moderation link). No Prompt 1/2 URL name,
template path, or model field was renamed or removed.

**Migration note:** the two new `Scholarship` fields
(`required_documents`, `application_instructions`) and the `is_active`
field require a migration. No migrations exist anywhere in this project
yet (see §1) — running `python manage.py makemigrations` as instructed
in §3 will pick up these fields along with everything from Prompts 1–2
in the same first migration.

---

## 2d. What Prompt 4 Added — the Recommendation Engine

Prompt 4 implements the deterministic Match Score (FR-06), Eligibility
(FR-07), and factual gap analysis on top of Prompts 1–3's models. No
model was added or changed in this prompt; no readiness, deadline
prioritization, Top Opportunities, Gemini/AI, or application logic
appears anywhere in it — those remain later prompts' work exactly as
scoped.

**The central Prompt 4 design decision — one shared, authoritative
criterion evaluator.** `apps/recommendations/services/criterion_evaluation_service.py`
is new (not one of Prompt 1's four originally-stubbed services, but a
fifth service the architecture called for): it is the ONLY place a
`ScholarshipCriterion` is ever compared against a `StudentProfile`.
Both `RecommendationService` (which needs a 0–100 `score_percent` per
criterion to weight) and `EligibilityService` (which needs an
`EligibilityStatus` per criterion) read from the exact same
`CriterionEvaluation` objects this service produces — neither
independently re-derives whether a criterion is satisfied. A dedicated
consistency test (`RecommendationEligibilityConsistencyTests`) proves a
failed criterion is reflected identically in `RecommendationResult.weak_criteria`
and in the corresponding `EligibilityResult.status` for the same
student/scholarship/criterion.

**No new eligibility status system.** `apps.common.enums.EligibilityStatus`
(ELIGIBLE / NOT_ELIGIBLE / MISSING_INFO), already defined since Prompt 1,
is used exactly as-is. "Missing Information" is deliberately never
conflated with "Not Eligible" — a student with a blank `location` field
against a LOCATION criterion, or an empty `skills` list against a
SKILLS criterion, gets `MISSING_INFO`, and `EligibilityService.rollup_status`
only ever produces overall `NOT_ELIGIBLE` when a **mandatory** criterion
is genuinely unmet (reusing `EligibilityResult.is_blocking`, a Prompt 1
property, unchanged). Overall eligibility is **not** derived from Match
Score — `test_eligibility_not_derived_from_match_score_alone` proves a
90% Match Score can still be overall Not Eligible when one mandatory
criterion fails.

**Match Score arithmetic is `Decimal`-only, matching Prompts 1–3's
existing convention** (`Scholarship.weights_are_valid()`,
`WeightService`). Every per-criterion contribution is summed at full
Decimal precision; only the final total is rounded, once, via
`ROUND_HALF_UP`, to match `RecommendationResult.match_score_percent`'s
`decimal_places=2`. Documented and tested against both a
non-trivial-precision case (33.33/33.33/33.34 summing to exactly
100.00) and the rounding mode itself.

**Scholarship validity is re-checked before scoring by reusing Prompt 3's
own rules, not by duplicating them** — `Scholarship.is_published`,
`is_active`, `is_expired`, and `WeightService.validate_configuration`
are all called as-is; `RecommendationService` raises a specific
`ValidationError` naming which check failed rather than silently
scoring an unpublished/expired/misconfigured scholarship.

**Security**: the new student-facing views
(`recommendations:my_recommendations`, `recommendations:detail`) take
**no student ID anywhere in their URLs** — both always operate on
`request.user`'s own `StudentProfile`, the same ownership-by-construction
pattern established for profile views in Prompt 2. There is nothing to
manipulate in the URL to reach another student's Match Score or
eligibility result; a dedicated test logs in as two different students
against the same scholarship and confirms each only ever sees their own
computed result.

**Gap analysis is intentionally minimal and factual.**
`apps/recommendations/services/gap_service.py` (new) reports exactly
`criterion` / `current_value` / `required_value` / `gap` / `status` for
every non-`ELIGIBLE` criterion — no suggestions, priorities, or
strategic language. `test_gap_report_contains_no_fabricated_advice`
asserts phrases like "you should" or "we recommend" never appear in a
gap report; that kind of guidance is explicitly reserved for a later,
AI-assisted prompt.

**A known, documented performance tradeoff**: the recommendation
detail view calls `compute_match_score`, `evaluate`, and `get_gaps`
independently, each internally running its own pass over the same
criteria list via `CriterionEvaluationService`. This is deliberate for
Prompt 4 — criteria lists are small (a handful per scholarship), and
threading one shared evaluation object across three differently-scoped
services would add real complexity for a cost that isn't being paid in
practice. What IS avoided is redundant **database queries** within any
single service call (each uses one `select_related('weight')` fetch of
the criteria list). `EligibilityService` and `GapService` do share one
persistence code path (`EligibilityService._persist_from_evaluations`)
so `EligibilityResult` rows are never written by two different pieces
of logic.

**Output contract for Prompt 5 (Readiness) and later prompts** — the
shapes below are now stable and should be read from, not re-derived:
- `RecommendationResult` (`apps.recommendations.models`): `match_score_percent`
  (Decimal, 2 places), `breakdown` (dict of `{criterion_type: 0 or 100}`),
  `weak_criteria` (list of `{criterion_type, status, explanation}` for
  every criterion that scored 0, whether `NOT_ELIGIBLE` or `MISSING_INFO`).
- `EligibilityResult` (one row per student/scholarship/criterion):
  `status` (`EligibilityStatus`), `explanation` (plain-language sentence),
  `quantified_gap` (e.g. `"+0.30 CGPA"`, blank if not numeric/not applicable),
  and the pre-existing `is_blocking` property.
- `EligibilityService.overall_status(student, scholarship)` /
  `EligibilityService.rollup_status(results)`: the single source of
  truth for a scholarship's overall eligibility for one student.
- `GapService.get_gaps(student, scholarship)` → `list[GapEntry]`
  (`criterion_type`, `criterion_display`, `current_value`,
  `required_value`, `gap`, `status`, `explanation`) — everything a
  Readiness or Profile-Improvement feature needs to know "what's
  missing," with zero advice baked in.

**Files touched this prompt:** `apps/recommendations/services/recommendation_service.py`
and `eligibility_service.py` (Prompt 1 stubs, now fully implemented —
signatures unchanged), `apps/recommendations/views.py`,
`apps/recommendations/urls.py` (added `my_recommendations`/`detail`,
`top_opportunities` untouched), `apps/recommendations/tests.py`
(extended), plus newly created `apps/recommendations/services/criterion_evaluation_service.py`
and `gap_service.py`, 2 new templates under `templates/recommendations/`,
and small additive edits to `templates/base/navbar.html` and
`templates/scholarships/student_detail.html` (added a "View My Match"
link for students, removed now-stale "later prompt" wording for FR-06/FR-07).
`apps/recommendations/services/deadline_service.py` and
`readiness_service.py` were **not** touched — both remain Prompt 1's
`NotImplementedError` stubs, exactly as scoped for later prompts.

---

## 2e. What Prompt 5 Added — Readiness, Deadline Intelligence & Top Opportunities

Prompt 5 fills in the two remaining Prompt 1 service stubs
(`ReadinessService`, `DeadlineService`) and replaces the Top
Opportunities placeholder with a real implementation. No model was
added or changed; no application-submission workflow, AI/Gemini logic,
or scholarship comparison feature appears anywhere in it.

**Readiness is NOT eligibility, and NOT Match Score — it is FR-08's own
"completed / required application items" concept**, kept as its own
separate calculation reading from Prompt 4's results, never re-deriving
them:
- "Required items" = every **mandatory** `ScholarshipCriterion` on the
  scholarship + every entry in `Scholarship.required_documents`
  (non-mandatory criteria are excluded, matching how
  `EligibilityService` already treats them as non-blocking).
- A criterion-derived item is "complete" only if `EligibilityService`'s
  own `EligibilityResult.status` for it is `ELIGIBLE` — read from
  Prompt 4, never recalculated.
- A document-derived item is **always** counted incomplete right now —
  documented explicitly as an honesty choice, not an oversight: no
  document-submission mechanism exists anywhere in the codebase yet
  (`Application.submitted_data` is an empty JSONField with no upload
  UI), and marking a document "complete" with zero evidence it was ever
  submitted would be fabricated information. This is the one line to
  change once a later prompt adds real submission tracking.
- Same `Decimal`-only arithmetic and single-`ROUND_HALF_UP`-at-the-end
  policy as `RecommendationService` (Prompt 4) — not reinvented, and a
  test (`test_matches_srs_worked_example_shape_4_of_5`) reproduces the
  SRS's own §4.3 worked example (5 required, 4 complete → 80%) using
  the current implementation's actual inputs.
- Zero required items is defined as 100% ready (an empty checklist is a
  completed checklist) rather than a `ZeroDivisionError` or an arbitrary
  fallback — documented and tested.

**Deadline urgency uses documented, transparent thresholds — not
invented silently.** FR-14 only gives three illustrative examples (🔴 2
days / 7 days / 20 days) without exact category boundaries, so
`DeadlineService` defines five categories (`EXPIRED`, `DUE_TODAY`,
`CRITICAL` ≤3 days, `URGENT` ≤7 days, `APPROACHING` ≤20 days,
`UPCOMING` beyond) whose boundaries are chosen to land on the SRS's own
example values, stated as three named constants in one place. Day
counting uses **calendar-date subtraction in the project's configured
local timezone** (`timezone.localtime`), not raw UTC second counting,
so a deadline later today correctly reads as "0 days / Due Today"
rather than an off-by-one artifact of mixing hours and days — this
distinction is exercised by dedicated tests for each urgency boundary.

**Top Opportunities (FR-20) ranks by two tiers, then Match Score, then
deadline — matching the SRS's own worked table, not an invented
combined score.** FR-20 explicitly defines "near-eligible" as a first-
class category ("does not currently satisfy one or more remediable
requirements but has a meaningful match score") that belongs in the
list alongside `ELIGIBLE` entries — this service honors that literally:
Eligible tier always ranks above Near-Eligible tier; within each tier,
entries sort by Match Score descending, then by soonest deadline as the
tie-break. A scholarship where the student matches on **nothing** (0%
Match Score) is excluded entirely — there's no SRS basis to call that
an "opportunity." Readiness is shown on every entry (as FR-20's own
table does) but is **not** used as a ranking key, since the SRS's own
worked example is sorted purely by Match Score. `recommended_action` is
a selection, not a generation — either the fixed phrase `"Apply now"`
(fully eligible and fully ready) or the first item of that
scholarship's own `ReadinessResult.action_plan`, never new wording
invented at this layer.

**Views extended, not duplicated**: `RecommendationDetailView` and
`MyRecommendationsView` (Prompt 4) now also call `ReadinessService` and
`DeadlineService` and pass their results straight through to the
templates — no scoring/readiness/deadline logic was added to the view
layer itself. `TopOpportunitiesView` replaces Prompt 1's placeholder
behind the **same URL name** (`recommendations:top_opportunities`), so
the existing navbar link required no changes. All three student-facing
views keep the Prompt 4 security property: none of them ever takes a
student ID in the URL, so there is nothing to manipulate to reach
another student's readiness, deadline, or ranked list — a dedicated
test asserts this structurally (checking the resolved URL itself
contains no student pk) in addition to the behavioral cross-student
tests carried over from Prompt 4's pattern.

**Dashboard integration is a thin read, not a second implementation.**
`apps/dashboard/views.py` now calls
`DeadlineService.top_opportunities(profile, limit=3)` directly for the
student dashboard's compact preview — it does not rank or score
anything itself, matching this app's existing "no business
calculations of its own" boundary from Prompt 1.

**Files touched this prompt:** `apps/recommendations/services/readiness_service.py`
and `deadline_service.py` (Prompt 1 stubs, now fully implemented —
signatures unchanged), `apps/recommendations/views.py` (extended, plus
`TopOpportunitiesPlaceholderView` replaced by `TopOpportunitiesView`),
`apps/recommendations/urls.py` (`top_opportunities` now points at the
real view; URL name unchanged), `apps/recommendations/tests.py`
(extended), `apps/dashboard/views.py` and `apps/dashboard/tests.py`
(extended), 1 new template (`recommendations/top_opportunities.html`,
replacing the removed `placeholder_top_opportunities.html`), and edits
to `recommendations/my_recommendations.html`,
`recommendations/recommendation_detail.html`, and
`dashboard/student_dashboard.html` to surface the new data (removing
now-stale "later prompt" wording for FR-08/FR-14/FR-20). No model, no
migration, and no existing Prompt 1–4 URL name, template path, or
behavior was removed.

---

## 2f. What Prompt 6 Added — Application Workflow, Provider Review & Bookmarks

Prompt 6 fully implements `ApplicationService` (Prompt 1's remaining
stub), builds the student apply/submit/track UI, the provider review
UI, and bookmark toggling. No model was added or changed — `Application`,
`ApplicationStatusHistory`, and `Bookmark` already existed with exactly
the fields needed since Prompt 1.

**The eligibility gate at submission time is deliberately not "must be
fully Eligible."** FR-09's own (Prompt 1) module docstring says submit
"must verify eligibility/readiness first" — Prompt 6 reads that
literally rather than assuming the strictest possible interpretation:
- Overall `NOT_ELIGIBLE` (a **mandatory** criterion genuinely unmet) —
  submission is **blocked**. There's no SRS basis for letting a student
  submit against a rule the scholarship itself says they fail.
- Overall `MISSING_INFO` — submission is **allowed**. The system
  lacking data about the student is a different fact than the student
  failing a requirement (FR-07's own three-way distinction), and FR-20
  already treats near-eligible scholarships as legitimate opportunities
  worth acting on.
- Readiness (FR-08) is **informational only** at submit time — it never
  blocks. This matters concretely: Prompt 5 documented that a required
  *document* item is always counted incomplete because no upload
  mechanism exists yet; if readiness gated submission, no student could
  ever submit to any scholarship with a required document listed. A
  dedicated test (`test_readiness_never_blocks_submission`) locks this in.

**Reapplication policy is now explicit, closing a gap Prompt 1's own
model docstring flagged and deferred.** A student MAY create a new
`DRAFT` `Application` for a scholarship they were previously `REJECTED`
for; the old `REJECTED` row and its full status history are never
mutated or deleted (FR-18 auditability). A student may NOT have two
simultaneously open (non-terminal) applications to the same scholarship
at once. Both directions are tested.

**Ownership follows the exact pattern `ScholarshipService` established
in Prompt 3** — `ApplicationService.get_owned_application_or_403`
(student's own application) and `get_provider_managed_application_or_403`
(provider reviewing an application to one of their own scholarships)
are the *only* ways any view fetches an `Application` by id; no view
uses a bare `get_object_or_404`. Cross-student and cross-provider
access attempts are tested at both the service layer and over real
HTTP requests (403 in every case).

**Status transitions are validated against Prompt 1's own
`ALLOWED_TRANSITIONS` dict** (unchanged) — skipping a stage (e.g.
`SUBMITTED` straight to `APPROVED`) or transitioning out of a terminal
status (`APPROVED`/`REJECTED`) raises `ValidationError` rather than
silently succeeding; every transition writes both an
`ApplicationStatusHistory` row and an `AuditLog` entry using the
existing `APPLICATION_STATUS_CHANGED` event type (no new enum value
needed — it was already defined in Prompt 1).

**A one-way, function-local dependency both directions — documented,
not hidden.** `ApplicationService.submit()` needs
`apps.recommendations.services.{eligibility_service,readiness_service}`
(FR-09's pipeline position requires it), and
`apps.scholarships.views.ScholarshipDetailView` needs
`apps.applications.models.Bookmark` to show the bookmark button state.
Since `apps.applications` already depends on `apps.scholarships` at
module level (for the `Scholarship` FK), a second *module-level*
import in the opposite direction would create a real circular import.
Both of these are function-body-local imports instead — by the time a
view actually runs, Django has already fully loaded every app, so this
is safe — and the project's module-level import graph was verified
programmatically to remain a clean, acyclic DAG (see §11).

**Bookmarks (FR-13)** — a simple POST-only toggle view plus a list
view, both operating only on the requesting student's own `Bookmark`
rows (same ownership-by-construction pattern as every other
student-facing view since Prompt 2).

**Views extended, not duplicated**: `MyApplicationsView` replaces
Prompt 1's placeholder behind the **same URL name**
(`applications:my_applications`), so the existing navbar link required
no changes.

**Files touched this prompt:** `apps/applications/services/__init__.py`
(Prompt 1 stub, now fully implemented — `ALLOWED_TRANSITIONS` and both
method signatures unchanged), `apps/applications/views.py` (full
rewrite), `apps/applications/urls.py` (extended), `apps/applications/tests.py`
(extended), `apps/scholarships/views.py` (added `is_bookmarked` context
to `ScholarshipDetailView` via a function-local import), `apps/dashboard/views.py`
and `apps/dashboard/tests.py` (added `pending_application_count` for
providers), 5 new templates under `templates/applications/`
(`apply_confirm.html`, `application_detail.html`, `provider_list.html`,
`provider_detail.html`, `my_bookmarks.html`), 1 renamed
(`placeholder_list.html` → `my_applications.html`, extended), and edits
to `templates/base/navbar.html`, `templates/scholarships/student_detail.html`
(added real Apply/Bookmark buttons, removed now-stale "later prompt"
wording for FR-09/FR-13), `templates/dashboard/student_dashboard.html`,
and `templates/dashboard/provider_dashboard.html`. No model, no
migration, and no existing Prompt 1–5 URL name, template path, or
behavior was removed.

---

## 2g. What Prompt 7 Added — the AI Advisory Layer

Prompt 7 fully implements the three Prompt 1 `ai_advisor` service stubs
(`FactsBundleService`, `GeminiService`, `AIAdvisorService`) and builds
the three student-facing AI surfaces: the Context-Aware AI Advisor
(FR-10), the AI Strategy Planner (FR-22), and the Profile Improvement
Advisor (FR-23). This is the guidance layer only — it explains and
prioritizes what Django already computed; it never computes anything
itself.

**The hard architectural rule from Prompt 1 is now load-bearing, not
aspirational.** `GeminiService` still imports nothing from
`apps.accounts`/`apps.scholarships`/`apps.recommendations`/`apps.applications`
— it only ever receives a plain `dict` (the Facts Bundle) and a prompt
string. Prompt 1's own architectural test
(`test_service_has_no_database_access_surface`, which inspects the
module's actual imports rather than trusting a comment) is kept
unmodified and re-run in Prompt 7 to confirm this held after
implementation, not just at stub time.

**`EligibilityStatus` still has exactly three values — Prompt 7 did not
add a fourth.** Prompt 7's own text refers to a "NEAR_ELIGIBLE" status
as if it were a peer of ELIGIBLE/NOT_ELIGIBLE/MISSING_INFO; it isn't.
Per Prompt 5's actual implementation, "near-eligible" is
`OpportunityEntry.is_near_eligible` — a derived boolean for
Top-Opportunities ranking/display, not a stored eligibility state. The
Facts Bundle keeps these structurally separate: every entry always
carries the real three-value `eligibility_status`, and only in
Top-Opportunities-derived contexts also carries `is_near_eligible` as
its own field — never merged into or mistaken for the status itself.
This distinction is enforced in the grounding instructions sent to
Gemini (rule 3) and covered by a dedicated test.

**`FactsBundleService` reads, tallies, and formats — it never
evaluates.** Every number in a bundle traces back to a call into
`RecommendationService`, `EligibilityService`, `GapService`,
`ReadinessService`, or `DeadlineService` — the exact same calls a
regular view would make. The one place this needed care: the Profile
Improvement Advisor's "affects N scholarships" counts are computed by
*tallying* how often each already-computed `GapEntry` recurs across the
catalog — this is aggregation of existing facts, not a new evaluation
of any criterion, and is documented as such in the module so a future
edit doesn't accidentally turn it into a second scoring system.

**Grounding, not post-hoc filtering, is how FR-19 is enforced.**
`GeminiService.GROUNDING_INSTRUCTIONS` is a fixed system prompt — sent
via the SDK's own `system_instruction` parameter, structurally
separate from the user's question — that: restricts Gemini to the
supplied Facts Bundle; forbids overriding any deterministic value;
requires the literal FR-19 refusal phrase when the bundle doesn't
cover the question; explicitly preserves the ELIGIBLE/NOT_ELIGIBLE/
MISSING_INFO distinction and the separate near-eligible framing;
forbids guaranteeing any outcome; and explicitly instructs Gemini to
treat any instruction embedded in the student's own question as
untrusted data, not a command (basic prompt-injection resistance). A
test asserts every one of these rules is actually present in the
instruction text, and a mocked-response test proves a "lying" Gemini
response (claiming a false 100% match) never touches the underlying
`RecommendationResult` row — `AIAdvisorService` only reads deterministic
results via `FactsBundleService`, it never writes to them.

**FR-22's fallback guarantee is implemented, not just described.**
`AIAdvisorService.generate_strategy` and `generate_profile_improvement`
build a deterministic, already-computed plain-text summary (reusing the
same ranked Top Opportunities / gap-tally data the Facts Bundle already
holds — no separate ranking logic) *before* calling Gemini, so that if
`GeminiServiceUnavailable` or `GeminiServiceError` is raised, the
student still gets that plain summary with `outcome=FALLBACK_NO_AI`
rather than an error page. `ask_advisor` (FR-10) has no deterministic
equivalent for a free-form question, so it reports the outage instead
— and a genuine Gemini-configured-but-failing request is recorded as
the new `AIResponseOutcome.ERROR` (see below), distinct from
"not configured," so a UI can tell the two apart. Because no test may
call the real Gemini API, every Gemini
interaction in the test suite is either exercised via the real
"not configured" fallback path (forced explicitly with
`@override_settings(GEMINI_API_KEY="", GEMINI_ENABLED=False)`, never
inferred from the local `.env`) or via `unittest.mock.patch` on
the `google.genai` SDK call itself for the success/error/
insufficient-info paths — never a live network request.

**One small additive enum change**: `apps.common.enums.AIResponseOutcome`
gained a fourth value, `ERROR` (Gemini configured but the request
itself failed — network, timeout, rate limit, or an empty/invalid
response), alongside the three that already existed
(`ANSWERED`/`INSUFFICIENT_INFO`/`FALLBACK_NO_AI`) — additive only,
matching every prior prompt's enum-extension convention, nothing
renamed or removed.

**Views stay thin — `AIAdvisorService` is the only module views call.**
None of the three views (`AIAdvisorView`, `StrategyPlannerView`,
`ProfileImprovementView`) touch `FactsBundleService` or `GeminiService`
directly. All three use `LoginRequiredMixin` (matching
`apps.recommendations.views`'s established convention from Prompts
4–6) and operate only on `request.user`'s own `StudentProfile` — no
student id appears in any AI URL, verified by a dedicated test that
checks the resolved URL string itself. `ai_advisor:advisor` keeps its
Prompt 1 URL name (only the view class changed); `strategy_planner`
and `profile_improvement` are new.

**Files touched this prompt:** `apps/ai_advisor/services/facts_bundle_service.py`,
`gemini_service.py`, `ai_advisor_service.py` (all three Prompt 1
stubs, now fully implemented — every method signature unchanged),
`apps/ai_advisor/views.py` (full rewrite), `apps/ai_advisor/urls.py`
(extended, `advisor` name unchanged), `apps/ai_advisor/tests.py`
(extended), `apps/common/enums.py` (1 new additive `AIResponseOutcome`
value), `requirements.txt` (updated a stale comment only — dependency
itself unchanged), 3 new templates under `templates/ai_advisor/`
(`advisor.html` replacing `placeholder_advisor.html`,
`strategy_planner.html`, `profile_improvement.html`), and edits to
`templates/base/navbar.html` and `templates/dashboard/student_dashboard.html`
(added links, removed now-stale "later prompt" wording for FR-10/22/23).
No model, no migration, and no existing Prompt 1–6 URL name, template
path, or behavior was removed.

---

## 3. Getting Started

```bash
# 1. Clone and enter the project
cd ssrams

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env: set DJANGO_SECRET_KEY, and either configure real SQL
# Server credentials (recommended — see §4) or set
# DB_ENGINE=sqlite_dev_fallback to get started immediately.

# 5. Apply migrations (they are committed — do NOT run makemigrations
#    unless you have changed a model; see §1)
python manage.py migrate

# 6. Create a superuser for Django Admin access
python manage.py createsuperuser

# 7. Run the test suite — expect "Ran 296 tests ... OK" (~15 min)
python manage.py test apps

# 8. Run the dev server
python manage.py runserver
```

Then visit `http://127.0.0.1:8000/` — it redirects to `/dashboard/`,
which redirects to `/accounts/login/` for anonymous users. Register a
student or provider account from there, or log in with the superuser
via `/admin/`.

### Running just one app's tests

```bash
python manage.py test apps.accounts
python manage.py test apps.scholarships
python manage.py test apps.recommendations
python manage.py test apps.applications
python manage.py test apps.ai_advisor
python manage.py test apps.audit
python manage.py test apps.dashboard
```

---

## 4. Database Configuration

**Production/team-development target: Microsoft SQL Server 2019+**
(SRS §2.1.3), via the `mssql-django` backend.

1. Install SQL Server (or use an existing instance / Docker container /
   Azure SQL). Create a database and a login with rights to it.
2. Install the **ODBC Driver 18 for SQL Server** for your OS:
   - Windows: usually already present; otherwise
     [Microsoft's installer](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server).
   - macOS: `brew install msodbcsql18 mssql-tools18` (via the
     `microsoft/mssql-release` tap — see Microsoft's Linux/macOS install
     docs, since Homebrew's own formula for this changes over time).
   - Linux (Debian/Ubuntu): follow Microsoft's `apt` instructions for
     `msodbcsql18` for your distro version.
3. Fill in `.env`: `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`,
   `DB_PORT` (1433 by default), and leave `DB_DRIVER`/`DB_EXTRA_PARAMS`
   at their `.env.example` defaults unless your setup needs different
   TLS/trust settings.
4. `DB_ENGINE=mssql` (the default — leave it alone).

**Temporary local fallback (SQLite):** if you just want to run
migrations/tests/server without SQL Server installed yet, set
`DB_ENGINE=sqlite_dev_fallback` in `.env`. This is explicitly a
stop-gap — see the big comment block in `config/settings.py` above that
branch. Don't develop a feature against it and assume it'll behave
identically to SQL Server (e.g. SQLite is far more permissive about
type coercion); switch to real SQL Server before merging feature work.

---

## 5. Database / Model Overview

```
User (accounts)
 ├── role: student | provider | admin
 ├── 1-to-1 → StudentProfile        (if role=student)
 │              ├── 1-to-many → RecommendationResult   (recommendations)
 │              ├── 1-to-many → EligibilityResult       (recommendations)
 │              ├── 1-to-many → ReadinessResult         (recommendations)
 │              ├── 1-to-many → Application             (applications)
 │              ├── 1-to-many → Bookmark                (applications)
 │              └── 1-to-many → AIInteraction           (ai_advisor)
 └── 1-to-1 → ProviderProfile       (if role=provider)
                ├── 1-to-many → ProviderVerification    (accounts)
                └── 1-to-many → Scholarship             (scholarships)
                                 ├── 1-to-many → ScholarshipCriterion
                                 │                └── 1-to-1 → ScholarshipCriterionWeight
                                 ├── 1-to-many → RecommendationResult
                                 ├── 1-to-many → EligibilityResult
                                 ├── 1-to-many → ReadinessResult
                                 ├── 1-to-many → Application
                                 └── 1-to-many → Bookmark

AuditLog (audit) → generic FK to any of the above (see apps/audit/models.py
                    docstring for why it uses a generic relation instead
                    of per-app foreign keys)
```

**The central v3.1 design decision** — scholarship-specific weights —
lives in `apps/scholarships/models.py`. Each `ScholarshipCriterion`
belongs to exactly one `Scholarship` and carries its own
`ScholarshipCriterionWeight`, so two different scholarships can weight
"CGPA" completely differently (FR-04, FR-06; SRS §2.4 — "there is no
fixed global weighting formula"). `Scholarship.weights_are_valid()`
enforces the "must sum to exactly 100%" rule using `Decimal` arithmetic
(never `float`, to avoid rounding false negatives). See the extensive
docstring in that file for the full reasoning, and
`apps/scholarships/tests.py` for the test that proves two scholarships
can weight the same criterion type differently.

**Scholarship lifecycle** (added in Prompt 3) is three independent
pieces rather than one status enum — `is_published` (the FR-04 publish
gate), `is_active` (moderation/withdrawal), and `is_expired` (derived
from `deadline`, not stored). See the "LIFECYCLE" section of
`apps/scholarships/models.py`'s module docstring for why the SRS's own
language ruled out a multi-state Draft/Submitted/Closed enum.

**Result/cache models** (`RecommendationResult`, `EligibilityResult`,
`ReadinessResult` in `apps/recommendations/models.py`) store the
*output* of the deterministic algorithms a later prompt implements —
not the algorithms themselves. This is what lets the Facts Bundle
(§7 below) and PR-02's ~2-second performance target both work: nothing
downstream recomputes a score that's already been cached.

---

## 6. Shared Service Architecture

**Views must stay thin.** A view's job is: parse the request, call a
service function, render the response. Calculation logic, Gemini
prompting, and multi-step business rules belong in a `services/` module
inside the app that owns that requirement — never inline in a view or
a template.

```
Scholarship Management (scholarships) — Prompt 3, fully implemented:
      │
ScholarshipService  — CRUD + get_owned_scholarship_or_403 (ownership choke point)
CriteriaService     — criterion CRUD, format-only validation (never evaluates a student)
WeightService       — Decimal-only 100%-sum validation (FR-04, FR-06)
PublicationService  — publish/unpublish gate (weights valid AND provider verified)
      │
      ▼
Student Profile (accounts)
      │
Scholarship Criteria + Weights (scholarships, from above)
      │
Recommendation Engine (recommendations) — Prompt 4, fully implemented:
      │
CriterionEvaluationService.evaluate_all()   — the ONE shared, authoritative
      │                                        per-criterion comparison (new in Prompt 4)
      ├──────────────────────┬─────────────────────────┐
      ▼                      ▼                          ▼
RecommendationService   EligibilityService         GapService
.compute_match_score()  .evaluate()                .get_gaps()
   → FR-06                 .overall_status()          → factual gap report,
   (Decimal weighted sum,     → FR-07                    reuses eligibility_service's
    ROUND_HALF_UP once)     (rollup honors                persistence path
                             is_mandatory / is_blocking)
      │
      ▼
ReadinessService.compute_readiness()              → FR-08, Prompt 5, fully implemented
      │
DeadlineService.weekly_priority_list()            → FR-14, Prompt 5, fully implemented
      │
DeadlineService.top_opportunities()               → FR-20, Prompt 5, fully implemented
      │
FactsBundleService.build_for_*()  (ai_advisor)     → SRS §2.1.1 (NOT YET IMPLEMENTED)
      │
GeminiService.generate_guidance() (ai_advisor)     → FR-19/22/23 (NOT YET IMPLEMENTED)
      │
AIAdvisorService.*()  (ai_advisor, orchestrates the two above)
```

The `scholarships`, `recommendations` (Recommendation Engine), and
Readiness/Deadline/Top-Opportunities blocks above are all real,
implemented code (Prompts 3–5), and as of Prompt 7 so is `ai_advisor`
(`FactsBundleService`, `GeminiService`, `AIAdvisorService`) — every
service block in this diagram is now fully implemented; none remain
`NotImplementedError` stubs. `EligibilityService` (via
`CriterionEvaluationService`) is, as originally planned, the ONLY place
that ever compares a `ScholarshipCriterion.required_value` against a
`StudentProfile`'s actual value — `apps.scholarships.services.CriteriaService`
validates that a criterion is well-formed, never whether a student
satisfies it; that boundary held exactly as designed once Prompt 4
implemented the other side of it. `ReadinessService` (Prompt 5) reads
`EligibilityService`'s results rather than re-evaluating criteria
itself, `DeadlineService.top_opportunities()` composes
`RecommendationService` + `EligibilityService` + `ReadinessService`
output directly rather than introducing a second recommendation engine
or a blended/invented ranking score, and `FactsBundleService` (Prompt
7) reads all of the above the same way — nothing in `apps.ai_advisor`
computes a score, gap, or readiness value; it only reads, tallies
existing facts, formats, and (via `GeminiService`) explains.

Every arrow above is a real function signature already defined in this
foundation (see `apps/*/services/`) — later prompts fill in the body,
not the shape. If you find yourself needing a different signature,
that's a legitimate design change; just update the docstring in the
same PR so the contract stays documented.

### The Gemini boundary (read this before touching `ai_advisor`)

`apps/ai_advisor/services/gemini_service.py`'s `GeminiService` class
takes a plain `dict` (`facts_bundle`) — never a Django model instance,
queryset, or anything with database access. This is deliberate: it
makes "Gemini cannot query the database directly" (SRS §2.1.1, FR-19)
true at the *type-signature* level, not just as a comment someone could
ignore. `apps/ai_advisor/tests.py` includes a test that inspects the
module's own imports to enforce this (kept from Prompt 1, re-verified
in Prompt 7 after full implementation) — don't import
`StudentProfile`/`Scholarship`/etc. into `gemini_service.py` even
transitively; keep all DB access in `FactsBundleService`, which hands
`GeminiService` a bundle that's already plain data. FR-19's "I don't
have enough information" rule is enforced by
`GeminiService.GROUNDING_INSTRUCTIONS` (a fixed system prompt, Prompt
7) rather than by filtering Gemini's output after the fact — see §2g
for the full reasoning, including why "near-eligible" is deliberately
NOT a fourth `EligibilityStatus` value.

---

## 7. App Responsibilities

| App | Owns | Key FRs |
|---|---|---|
| `apps.accounts` | Auth, `User`, roles, Student/Provider profiles, provider verification | FR-01, FR-02, FR-03 |
| `apps.scholarships` | Scholarships, criteria, provider-defined weights | FR-04, FR-05 |
| `apps.recommendations` | Match scoring, eligibility, gaps, readiness, deadlines, Top Opportunities, comparison | FR-06, FR-07, FR-08, FR-14, FR-20, FR-21 |
| `apps.applications` | Applications, status lifecycle, provider review, bookmarks | FR-09, FR-13, FR-16 |
| `apps.ai_advisor` | Facts Bundle, Gemini integration, Strategy Planner, Profile Improvement Advisor, AI Advisor | FR-10, FR-11, FR-19, FR-22, FR-23 |
| `apps.dashboard` | Role-specific dashboard composition (no models of its own) | FR-15 |
| `apps.audit` | Audit logging, used by every other app | FR-18 |
| `apps.common` | RBAC decorators/mixins, shared enums, middleware, context processor, `TimeStampedModel` — cross-cutting utilities, not a "feature" app | — |

---

## 8. Environment Variables

See `.env.example` for the full, commented list. Highlights:

- `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` — standard
  Django settings; never commit a real `DJANGO_SECRET_KEY`.
- `DB_ENGINE` — `mssql` (default/intended) or `sqlite_dev_fallback`
  (temporary local-only, see §4).
- `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` /
  `DB_DRIVER` / `DB_EXTRA_PARAMS` — only used when `DB_ENGINE=mssql`.
- `GEMINI_API_KEY` — server-side only, read in `config/settings.py` and
  nowhere else in the code except through `settings.GEMINI_API_KEY`
  inside `GeminiService`. Never passed to a template or exposed to the
  frontend. Leave blank to run with AI features disabled — every
  deterministic feature keeps working per SRS §2.5, and
  `settings.GEMINI_ENABLED` / `{{ gemini_enabled }}` in templates
  reflect this so the UI can show an honest "not configured" state
  instead of pretending the feature works.

---

## 9. Security Notes (SRS §3.3.3)

Already in place at the foundation level:
- Passwords hashed via Django's default (`PBKDF2`) — never touched
  directly.
- CSRF protection on by default (`CsrfViewMiddleware` + `{% csrf_token %}`
  in every form).
- `SESSION_COOKIE_HTTPONLY = True`; `CSRF_COOKIE_SECURE` /
  `SESSION_COOKIE_SECURE` auto-enabled whenever `DEBUG=False`.
- `X_FRAME_OPTIONS = "DENY"`, `SECURE_CONTENT_TYPE_NOSNIFF`,
  `SECURE_BROWSER_XSS_FILTER`.
- HSTS auto-configured when `DEBUG=False` (`DJANGO_HSTS_SECONDS`).
- `apps.common.rbac` gives every later view a tested way to restrict
  by role (`role_required`, `RoleRequiredMixin`) or by object ownership
  (`OwnerRequiredMixin`) instead of ad-hoc `if` checks scattered across
  views.
- The Gemini API key never leaves the server process (see §6/§8).

Still to be done in later prompts as each feature is built: rate
limiting on AI endpoints (PR-05 mentions Gemini latency, not rate
limits specifically, but it's good practice), parameterized-query
verification once raw SQL (if any) is introduced, and a security
review pass once real user-facing forms beyond auth exist.

---

## 10. Project Structure

```
ssrams/
├── manage.py
├── requirements.txt
├── .env.example
├── .gitignore
├── config/                      # Django project package
│   ├── settings.py              # SQL Server + env config, security defaults
│   ├── urls.py                  # Root router — one include() per app namespace
│   ├── wsgi.py / asgi.py
├── apps/
│   ├── common/                  # RBAC, enums, middleware, context processor
│   ├── accounts/                # User, StudentProfile, ProviderProfile
│   ├── scholarships/            # Scholarship, Criterion, CriterionWeight
│   ├── recommendations/         # Result models + 4 service interfaces
│   ├── applications/            # Application, StatusHistory, Bookmark
│   ├── ai_advisor/               # AIInteraction + 3 service interfaces
│   ├── dashboard/                # Role-aware dashboard (no models)
│   └── audit/                    # AuditLog + fully-implemented log_event()
├── templates/
│   ├── base/                     # base.html, navbar, footer, placeholder partial
│   ├── accounts/                 # login, register (student/provider)
│   ├── dashboard/                # 3 role-specific dashboards
│   ├── scholarships/ recommendations/ applications/ ai_advisor/  # placeholders
├── static/css/base.css
└── media/                        # user uploads (gitignored except .gitkeep)
```

---

## 11. Verification Performed On This Codebase

**§11a below records the real execution verification (296 tests passing).**
The list immediately following is the *static* review that was performed
while the code was being written in a sandbox with no Django install, as
a substitute for `python manage.py check`. It is kept for the record —
every item still holds — but it is no longer the strongest evidence
available:

- Every `.py` file (77 total) parses with zero syntax errors (`ast.parse`).
- Cross-app import graph traced by hand: strictly one-directional
  (`common ← accounts ← scholarships ← recommendations ← applications ←
  ai_advisor ← dashboard`), zero circular imports. `apps.audit` imports
  nothing from other apps' model modules (uses a generic FK + a lazy
  `"accounts.User"` string reference).
- Every `ForeignKey`/`OneToOneField`'s `related_name` checked
  programmatically for collisions on the same target model — none found.
- Every model in every app confirmed registered in that app's `admin.py`
  (directly or via inline, e.g. `ApplicationStatusHistory` as an inline
  on `ApplicationAdmin`).
- `INSTALLED_APPS` contains exactly the 7 local apps, no more, no less.
- `MIDDLEWARE` order checked against Django's documented requirements
  (Security → Session → Common → CSRF → Auth → Messages → Clickjacking
  → project's own `RoleContextMiddleware`, correctly placed after
  `AuthenticationMiddleware` since it reads `request.user`).
- Every `include(..., namespace=X)` in `config/urls.py` matched against
  that app's own `app_name = "X"`.
- Every `{% url %}` tag in every template and every `reverse()` call in
  Python code matched against an actual defined URL name.
- Every view's `template_name` (or `render()` call) matched against an
  actual file under `templates/`.
- Every template's Django block tags (`{% if/for/block/with/comment %}`)
  checked for balanced open/close pairs; `{% extends %}` confirmed to
  be the first template tag in every child template.
- `Scholarship.weights_are_valid()` confirmed to use `Decimal`
  arithmetic throughout (no float-precision risk in the "sums to
  exactly 100%" check).
- `AuditLog.EventType` confirmed to include a distinct entry for every
  event FR-18 names explicitly (login/logout, provider approval,
  scholarship changes, weight changes, status changes, suspensions).

**Superseded by §11a.** This paragraph previously read that migrations
generating cleanly, the ORM's actual SQL, template rendering, and the
test suite passing end-to-end were all unverified because the sandbox had
no Django install. All four have since been verified by actually running
the project — see §11a. What remains unverified is narrower and is listed
in §1: SQL Server as the backend (the suite ran on `sqlite_dev_fallback`),
and live Gemini API calls (always mocked).

### 11a. Execution verification — 2026-08-31 (Python 3.11.3 / Django 5.2.3)

The project was installed and run for the first time. Commands, from
`ssrams/` with `DB_ENGINE=sqlite_dev_fallback`:

| Command | Result |
| --- | --- |
| `python manage.py check` | System check identified no issues (0 silenced) |
| `python manage.py makemigrations` | 12 files generated across 6 apps, now committed |
| `python manage.py makemigrations --check --dry-run` | **No changes detected** — no model/migration drift |
| `python manage.py test apps` | **Ran 296 tests … OK** — 0 failures, 0 errors, exit 0 |

First execution found 13 problems (4 failures + 9 errors). All are fixed;
the causes, for the record:

1. **9 errors — `ModuleNotFoundError: No module named 'google'`.** Every
   `GeminiService` test patches the Gemini SDK, and `mock.patch` must
   import a module before it can patch it. Cause was environmental, not a
   code defect: the Gemini dependency in `requirements.txt` had never been
   installed. Fixed by installing it. **The tests still patch the
   SDK — no test makes a real network call**, and the boundary described
   in §2g is unchanged. (Superseded by §11b: the package installed at the
   time was the end-of-life pre-1.0 client, while `gemini_service.py`
   was written against the current `google-genai` SDK. See §11b.)
2. **1 failure — `AIInteraction` ordering was non-deterministic.**
   `test_ordering_is_most_recent_first` failed because ordering on
   `created_at` alone is ambiguous when two rows are created inside the
   same clock tick, and the views read "the latest interaction" with
   `.first()`. This was a genuine model bug. Fixed by making the tie-break
   explicit — `ordering = ["-created_at", "-id"]` — applied consistently
   to every model with the same exposure, which is what the six
   `0002_alter_*_options` migrations contain.
3. **2 failures — HTML escaping in a test assertion.** The scholarship
   catalog tests asserted `assertContains(response, "Published & Visible")`
   against a template that correctly escapes `&` to `&amp;`. The template
   was right and the assertion was wrong; fixed with `html=True`.
4. **1 error — `NameError: name 'g' is not defined`** in
   `test_gap_report_contains_no_fabricated_advice`: a comprehension
   referenced its loop variable from outside the generator. Test-only
   typo; fixed.
5. **1 failure — an over-broad privacy assertion.**
   `test_advisor_bundle_never_includes_another_students_data` asserted the
   bare string `"2"` (student B's pk) was absent from the serialized facts
   bundle. `"2"` legitimately appears in `2026` deadlines and amounts, so
   the assertion could never pass. **This was a test defect, not a data
   leak** — the bundle contained none of student B's data. Fixed by
   asserting on identifying values rather than a bare pk digit.

Two hardening changes landed in the same pass. Neither was a test
failure; both were found by reading the rendered output above:

- **Logout is now a CSRF-protected POST, not a GET link.** A `GET`
  logout can be triggered by any third-party page (an `<img>` tag is
  enough), and Django 5 dropped GET-logout support regardless. The
  navbar now submits a form, `logout_view` is decorated `@require_POST`,
  and the accounts tests assert the POST-only contract.
- **`toggle_bookmark` validates its redirect target.** `next` and the
  `Referer` header are attacker-controllable, so they are checked with
  `url_has_allowed_host_and_scheme` before use; otherwise the view is an
  open redirect that can bounce a logged-in student to a look-alike
  phishing page.

Reproducing this run: `./runtests.sh <app> …` (repo root) runs each app in
its own process and logs to `testlog.txt`; `dj.sh` wraps `manage.py` with
the same environment. Both are local verification helpers, not part of
the SSRAMS deliverable.

### 11b. Gemini SDK correction + first live request — 2026-08-31

§11a resolved a `ModuleNotFoundError` by installing "the Gemini
dependency". The package installed was the **pre-1.0 Google generative
client**, while `apps/ai_advisor/services/gemini_service.py` was written
against the **current `google-genai` SDK** (`from google import genai`;
`genai.Client(...)`; `client.models.generate_content(...)`). Those are
different distributions with incompatible call styles.

Consequence: the service's lazy `from google import genai` raised
`ImportError` on **every** request. The boundary handled that correctly —
`GeminiServiceUnavailable` → deterministic fallback — so nothing crashed
and nothing was logged as an error. The AI Advisor simply returned its
"AI is not configured" fallback forever, with a valid `GEMINI_API_KEY`
present. A fallback that is indistinguishable from success is the reason
this went unnoticed.

Corrected:

- Installed `google-genai` (2.20.0) and **uninstalled** the end-of-life
  client so the two generations cannot coexist. `requirements.txt` pins
  `google-genai>=1.0,<3` and its status block now matches reality.
- `apps/ai_advisor/tests.py` had been saved wrapped in a Markdown
  ```` ```python ```` fence, so the module raised `SyntaxError` and
  **none of the ai_advisor tests ran at all**. Fence removed.
- The "Gemini not configured" tests relied on the developer's `.env`
  having no key. Once a real key exists, `GEMINI_ENABLED` is True at
  settings-import time and those tests issue live, billable,
  non-deterministic requests. They now force the precondition with
  class-level `@override_settings(GEMINI_API_KEY="", GEMINI_ENABLED=False)`.
  Assertions are unchanged.
- Added `GeminiServiceSdkBoundaryTests`: fails the build if the legacy
  SDK's module path or call style reappears, if the declared SDK is not
  the importable one, if the key/model do not reach
  `genai.Client(api_key=...)` / `generate_content(model=...)`, or if the
  request loses its timeout.
- **Model configuration was invalid.** `.env.example` advertised
  `gemini-1.5-pro`, which is retired (404). The configured
  `gemini-3.7-flash` returned 503/429 on 4/4 attempts — the catalog
  (`client.models.list()`) lists models a key is not entitled to, and
  that only surfaces at request time. Verified reachable:
  `gemini-3.6-flash`, `gemini-3.5-flash`. Default is now
  `gemini-3.6-flash`.
- Added `GEMINI_TIMEOUT_SECONDS` (default 90) — a live answer measured
  64.7s, and the call had no bound, so a hung upstream request could pin
  a worker indefinitely. **Set any WSGI server timeout above it**
  (`gunicorn --timeout 120`; the default 30s is too low).

First live verification of the request path, through a running
`manage.py runserver` over real HTTP (temporary student, deleted
afterwards):

| Step | Result |
| --- | --- |
| `python manage.py check` | no issues |
| `python manage.py test apps.ai_advisor` | **41 tests, OK** |
| `python manage.py test` (whole project) | **302 tests, OK** |
| `POST /ai/advisor/` (FR-10) | 200 · `answered` · 16734 ms · rendered |
| `POST /ai/strategy/` (FR-22) | 200 · `answered` · 2414 chars |
| `POST /ai/improve/` (FR-23) | 200 · `answered` · 1660 chars |
| `gemini_model_name` on all three | `gemini-3.6-flash` |

Grounding held on the live responses:

- The scholarship whose deterministic `eligibility_status` was
  `missing_info` was reported as `missing_info`, not upgraded to
  eligible; `is_near_eligible` stayed a separate field.
- FR-22 run against a student with **no** Top Opportunities returned
  `insufficient_info` with the exact FR-19 refusal phrase rather than
  inventing a plan. Re-run with a student who has opportunities, the
  same endpoint returned `answered`. That is the refusal behaving as
  specified, not a failure — worth knowing before someone "fixes" it.

### Prompt 2 verification (same sandbox constraint as §1 — see there)

> The six per-prompt subsections below are the **historical** static
> review notes, written before the code could be executed. Their
> "(same sandbox constraint as §1)" headings refer to the constraint §1
> used to describe; that constraint is gone — see §11a for the actual
> execution results that supersede them. They are kept because they
> document *why* each design decision was made, which the test output
> does not.

- All 6 files touched plus all newly created files (11 templates,
  1 test-file extension) parse/check clean with the same `ast`-based
  syntax pass used in Prompt 1.
- Every `reverse()` call added to `apps/accounts/tests.py` and every
  `{% url %}` tag added across all templates was cross-checked against
  the URL names actually defined in `apps/accounts/urls.py` — including
  confirming `admin_verification_detail` is always called with its
  required `provider.pk` argument.
- Confirmed the two existing Prompt 1 registration tests
  (`test_student_registration_creates_account_with_correct_role`,
  `test_provider_registration_creates_account_and_profile`) still pass
  their (now-required) additional profile fields — they were updated
  in place rather than left to silently break against the extended
  forms.
- Confirmed no Prompt 1 URL name, template path, or model field was
  renamed or removed — every change in this prompt is additive.

### Prompt 3 verification (same sandbox constraint as §1 — see there)

- All 12 files touched/created (6 new: `forms.py` + 5 files under
  `services/`; 6 edited) parse clean with the same `ast`-based syntax
  pass used in Prompts 1–2, run across the full project (83 `.py` files
  total, up from 77).
- Unused-import check re-run on `apps/scholarships/views.py` after
  writing it — caught and removed two imports (`AuditLog`, `log_event`)
  that were superseded by the services layer already calling
  `log_event` itself; views correctly never call it directly.
- All 12 new templates under `templates/scholarships/` (plus 2 edited
  dashboard templates) checked for balanced `{% if/for/block/with %}`
  tags — 33 templates project-wide, all balanced.
- Every `{% url 'scholarships:...' %}` tag (across templates AND the
  view code that builds redirects) and every `reverse("scholarships:...")`
  call in the new tests cross-checked against the 15 URL names actually
  defined in `apps/scholarships/urls.py` — 1:1 match, including
  confirming multi-argument routes (`provider_criterion_edit`,
  `provider_criterion_delete`) are always called with both required
  path arguments.
- Import graph re-verified acyclic after `apps.scholarships` gained new
  imports from `apps.audit`: confirmed `apps/audit/*.py` (excluding its
  own test file) contains zero real imports of `apps.scholarships` —
  only docstring mentions of the word "scholarships".
- Confirmed no new `ForeignKey`/`OneToOneField` was added to any
  scholarships model in this prompt (only plain fields), so the
  `related_name` collision check performed in Prompt 1 still holds
  without needing to be re-run from scratch.
- `WeightService`'s Decimal-only arithmetic was specifically tested
  against a case that fails under naive float summation (33.33 + 33.33
  + 33.34) to confirm the "no float-precision risk" property established
  in Prompt 1 (`Scholarship.weights_are_valid()`) was carried through
  correctly into the new service layer rather than silently regressing.

### Prompt 4 verification (same sandbox constraint as §1 — see there)

- All files touched/created (2 new: `criterion_evaluation_service.py`,
  `gap_service.py`; several implemented/edited) parse clean with the
  same `ast`-based syntax pass used in Prompts 1–3, run across the full
  project (85 `.py` files total, up from 83; 35 templates, up from 33).
- Caught and fixed a self-inconsistent test while writing this section:
  an early draft of `test_rounding_behavior_half_up` asserted a rounding
  boundary using a criterion weight (`33.335`) that has 3 decimal
  places, which `ScholarshipCriterionWeight.weight_percent`
  (`decimal_places=2`) cannot actually store — rewritten to use a
  schema-valid weight (`33.33`/`66.67`, both exactly representable) for
  the behavioral assertion, plus a separate, explicit assertion of the
  `ROUND_HALF_UP` policy itself against a real `.xx5` Decimal value, so
  the test no longer relies on data the database would silently
  truncate.
- Every `reverse("recommendations:...")` call in the new tests
  cross-checked against `apps/recommendations/urls.py`'s actual defined
  names — 1:1 match. Also re-ran the same cross-check for
  `apps/accounts/tests.py`, `apps/scholarships/tests.py`, and
  `apps/dashboard/tests.py` as a regression check; all still match
  (the one intentional non-match, `accounts:register_admin`, is a
  Prompt 2 test that asserts that URL name does NOT exist, wrapped in
  `assertRaises(NoReverseMatch)` — confirmed not a regression).
- Import graph re-verified acyclic: `apps.recommendations` gained new
  imports from `apps.audit` and `apps.scholarships.services.weight_service`;
  confirmed neither `apps/scholarships/*.py` nor `apps/accounts/*.py`
  contains any real (non-docstring) import of `apps.recommendations`.
- Confirmed `apps/recommendations/services/deadline_service.py` and
  `readiness_service.py` are byte-for-byte unchanged from Prompt 1 —
  still `NotImplementedError` stubs, as required by this prompt's scope.
- Confirmed no model file was modified in this prompt (only
  `services/`, `views.py`, `urls.py`, `tests.py`, and templates), so no
  new migration is introduced beyond what Prompts 1–3 already require.

### Prompt 5 verification (same sandbox constraint as §1 — see there)

- All files touched (no new `.py` files this prompt — only Prompt 1's
  two remaining service stubs filled in, plus views/urls/tests/templates
  edited) parse clean with the same `ast`-based syntax pass used in
  Prompts 1–4, run across the full project (still 85 `.py` files; 35
  templates, net unchanged — 1 new `top_opportunities.html` added, 1
  placeholder template removed).
- Caught and fixed a real logic bug while writing `DeadlineService.days_remaining`:
  an early draft mixed `total_seconds() // 86400` arithmetic with an
  incorrectly-ordered same-day check that could misclassify a deadline
  falling very early or very late in the local calendar day. Rewrote it
  to use calendar-date subtraction in local time
  (`timezone.localtime(...).date()` on both sides) instead, which is
  both simpler and structurally correct — verified against dedicated
  tests for each urgency boundary (`CRITICAL`, `URGENT`, `APPROACHING`,
  `UPCOMING`, `EXPIRED`).
- Verified `EligibilityService.rollup_status` (Prompt 4) is still called
  exactly once per view/service invocation needing it — `top_opportunities()`
  and the detail view both use the already-computed `eligibility_results`
  list rather than triggering a second internal evaluation pass, keeping
  Prompt 4's redundant-evaluation tradeoff from silently growing worse.
- Every `reverse("recommendations:...")` and `reverse("dashboard:...")`
  call in the extended/new tests cross-checked against each app's actual
  `urls.py` — 1:1 match, re-run across **all six** apps with a `urls.py`
  as a full regression check (accounts, scholarships, recommendations,
  applications, ai_advisor, dashboard), not just the app touched this
  prompt.
- Confirmed the new `apps.dashboard → apps.recommendations` import
  (added for the Top Opportunities dashboard preview) is one-directional:
  `apps/recommendations/*.py` contains zero real imports of
  `apps.dashboard` (the two textual matches found are docstring/comment
  mentions of the word "dashboards," not import statements).
- Confirmed no model file was modified in this prompt either, so no
  additional migration is introduced beyond what Prompts 1–3 already
  require — `makemigrations` still only needs to run once, at the start,
  to cover everything through Prompt 5.

### Prompt 6 verification (same sandbox constraint as §1 — see there)

- All files touched (no new `.py` files — only Prompt 1's remaining
  `ApplicationService` stub filled in, plus views/urls/tests/templates)
  parse clean with the same `ast`-based syntax pass used in Prompts
  1–5, run across the full project (still 85 `.py` files; 40 templates,
  up from 35 — 5 new under `templates/applications/`, plus the Prompt 1
  placeholder renamed and extended rather than replaced).
- **Import graph re-verified programmatically, not just by grep.** This
  prompt introduces two directions of dependency between apps that
  already depend on each other one way (`apps.applications` module-level
  depends on `apps.scholarships`; this prompt adds a function-local
  `apps.scholarships → apps.applications` import, plus a function-local
  `apps.applications → apps.recommendations` import). A small script
  parsed every app's `.py` files (excluding `tests.py`), collected only
  **module-level** `from apps.X import ...` / `import apps.X` lines
  (ignoring anything indented inside a function body), built the
  resulting dependency graph, and ran a cycle-detection pass — the
  result is a clean, acyclic DAG with zero cycles. This is stronger
  evidence than a manual grep, which can't distinguish a module-level
  import from a deliberately-deferred function-local one.
- Every `reverse("applications:...")` call across the extended test
  file cross-checked against `apps/applications/urls.py`'s actual
  defined names — 1:1 match, and the same full six-app regression sweep
  from Prompt 5 was re-run (accounts, scholarships, recommendations,
  applications, ai_advisor, dashboard) with no unexpected mismatches.
- Confirmed `ApplicationService.ALLOWED_TRANSITIONS` (Prompt 1, unchanged)
  is the only place transition legality is decided — no view or template
  hard-codes an alternate list of allowed next statuses; the provider
  review template renders whatever `ApplicationService.ALLOWED_TRANSITIONS`
  says is legal for the application's current status, so the two can
  never drift apart.
- Confirmed no model file was modified in this prompt, so no additional
  migration is introduced beyond what Prompts 1–3 already require.

### Prompt 7 verification (same sandbox constraint as §1 — see there)

- All files touched (no new `.py` files — only Prompt 1's three
  remaining `ai_advisor` service stubs filled in, plus
  views/urls/tests/templates and one additive enum value) parse clean
  with the same `ast`-based syntax pass used in Prompts 1–6, run across
  the full project (still 85 `.py` files; 42 templates, up from 40 — 3
  new under `templates/ai_advisor/`, 1 placeholder removed).
- **Re-ran Prompt 1's own architectural boundary test unmodified**
  (`test_service_has_no_database_access_surface`, which inspects
  `gemini_service.py`'s actual module namespace for the forbidden model
  names rather than trusting a comment) to confirm it still passes
  after full implementation, and added a companion check
  (`test_grounding_instructions_cover_required_rules`) asserting every
  Prompt 7 grounding requirement is textually present in the system
  instruction actually sent to Gemini.
- **No live network/API calls anywhere in the test suite.** Every test
  that needs a "Gemini responded with X" scenario uses
  `unittest.mock.patch("google.genai.Client")` to intercept the SDK call
  itself, never a real request. Tests that need the "not configured"
  path force it explicitly with
  `@override_settings(GEMINI_API_KEY="", GEMINI_ENABLED=False)` at class
  level — they must NOT rely on the local `.env` happening to have no
  key, because once a real key is present `GEMINI_ENABLED` becomes True
  at settings-import time and those tests would issue live, billable,
  non-deterministic requests. See `GeminiServiceSdkBoundaryTests`.
- **Caught and fixed a real distinction, not a bug**: Prompt 7's own
  text speaks of "NEAR_ELIGIBLE" as if it's a fourth `EligibilityStatus`
  value. Cross-checking against `apps/common/enums.py` (Prompt 1) and
  `apps/recommendations/services/deadline_service.py` (Prompt 5)
  confirmed it is not — it's `OpportunityEntry.is_near_eligible`, a
  separate derived boolean. `FactsBundleService` and the grounding
  instructions were written to keep the two structurally distinct
  rather than silently inventing a fourth status value, which would
  have violated Prompt 7's own instruction not to introduce a second
  scoring/status system. A dedicated test
  (`test_strategy_bundle_includes_near_eligible_as_separate_field_not_a_status`)
  locks this in.
- A dedicated test (`test_match_score_unchanged_after_strategy_generation`)
  feeds a mocked, deliberately false Gemini response (claiming a 100%
  match score) through the full `AIAdvisorService.generate_strategy`
  path and confirms the underlying `RecommendationResult` row is
  byte-for-byte unchanged afterward — direct evidence that Gemini
  cannot influence a deterministic result even if its output text is
  wrong, not just an architectural claim.
- Import graph re-verified programmatically (same script introduced in
  Prompt 6): `apps.ai_advisor` now depends on `apps.accounts`,
  `apps.scholarships`, `apps.recommendations`, and `apps.applications`
  at module level — all one-directional, confirmed via the same
  cycle-detection pass; result remains a clean, acyclic DAG.
- Confirmed no model file was modified in this prompt (only
  `apps/common/enums.py`'s additive `AIResponseOutcome.ERROR` value,
  which needs no migration beyond a `CharField(choices=...)` update),
  so no additional migration is introduced beyond what Prompts 1–3
  already require.

