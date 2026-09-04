"""
apps/recommendations/services/deadline_service.py

Prompt 1 established this interface (signatures below are UNCHANGED
from that stub). Prompt 5 fully implements it.

--------------------------------------------------------------------
TIMEZONE HANDLING (SS9) -- Django's utilities only
--------------------------------------------------------------------
All "now" values come from django.utils.timezone.now(), which returns
a timezone-aware datetime in UTC, compared against
Scholarship.deadline (a DateTimeField -- USE_TZ=True is set in
config/settings.py per Prompt 1, so Django stores/returns this as
timezone-aware too). No naive datetime is ever constructed or compared
here; no client-supplied "current time" is ever trusted as
authoritative (SS9: "Do not use client-provided arbitrary dates as the
authoritative current time").

--------------------------------------------------------------------
URGENCY THRESHOLDS -- SRS gives EXAMPLES, not exact thresholds; this
is a documented, transparent rule (SS8), not a hidden invented one.
--------------------------------------------------------------------
FR-14 shows three illustrative urgency markers -- "🔴 2 days, 🔴 7
days, 🔴 20 days" -- without stating the exact boundaries between
urgency categories. Per the Prompt 5 brief SS8 ("If the SRS does not
define exact thresholds, establish a simple, transparent deterministic
rule and document it clearly"), this service defines five categories
whose boundaries are chosen to land close to the SRS's own three
example values (2, 7, 20) while covering the required "far / approaching
/ very close / today / passed" states from SS8:

    days_remaining < 0                  -> EXPIRED
    days_remaining == 0                 -> DUE_TODAY
    0 < days_remaining <= 3             -> CRITICAL   (near the SRS's "2 days" example)
    3 < days_remaining <= 7             -> URGENT      (matches the SRS's "7 days" example exactly)
    7 < days_remaining <= 20            -> APPROACHING (matches the SRS's "20 days" example exactly)
    days_remaining > 20                 -> UPCOMING

These thresholds are three named module-level constants
(_CRITICAL_MAX_DAYS, _URGENT_MAX_DAYS, _APPROACHING_MAX_DAYS below) --
change them in exactly one place if a later requirement states exact
SRS values.

A scholarship whose deadline has passed is NEVER presented as an
active opportunity (SS8: "A passed deadline must never be presented as
an active opportunity") -- see Scholarship.is_visible_to_students
(Prompt 3), which already excludes expired scholarships from student
discovery; DeadlineService.days_remaining/urgency are still computable
for an expired scholarship (so a provider/admin view can show "this
expired N days ago"), but Top Opportunities (this module,
top_opportunities()) filters expired scholarships out entirely before
ranking, on top of the is_visible_to_students filter already applied
by its caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import StudentProfile
from apps.common.enums import EligibilityStatus
from apps.scholarships.models import Scholarship

from .eligibility_service import EligibilityService
from .readiness_service import ReadinessService
from .recommendation_service import RecommendationService, _validate_scholarship_recommendable


class DeadlineUrgency:
    """
    Plain string constants (not a Django TextChoices, since urgency is
    never stored on a model -- it is always computed fresh from
    Scholarship.deadline at read time, same as Scholarship.is_expired
    from Prompt 3). No new enum system is introduced on top of
    apps.common.enums; this is a small, local, self-contained set.
    """

    EXPIRED = "expired"
    DUE_TODAY = "due_today"
    CRITICAL = "critical"
    URGENT = "urgent"
    APPROACHING = "approaching"
    UPCOMING = "upcoming"

    LABELS = {
        EXPIRED: "Expired",
        DUE_TODAY: "Due Today",
        CRITICAL: "Critical",
        URGENT: "Urgent",
        APPROACHING: "Approaching",
        UPCOMING: "Upcoming",
    }


# See module docstring "URGENCY THRESHOLDS" for the reasoning behind
# these exact numbers.
_CRITICAL_MAX_DAYS = 3
_URGENT_MAX_DAYS = 7
_APPROACHING_MAX_DAYS = 20


@dataclass
class OpportunityEntry:
    """
    One ranked Top Opportunities row (FR-20): Match Score + Eligibility
    + Readiness + Deadline + one Recommended Action, exactly the five
    fields FR-20's worked table shows. Plain dataclass, not a model --
    composed fresh from RecommendationResult / EligibilityResult /
    ReadinessResult / Scholarship each time (no new cache table; SS10
    "Do not create a second recommendation engine").
    """

    scholarship: Scholarship
    match_score_percent: Decimal
    eligibility_status: str
    is_near_eligible: bool
    readiness_percent: Decimal
    days_remaining: int
    urgency: str
    recommended_action: str


class DeadlineService:
    """Deterministic deadline urgency + weekly priority list (FR-14)."""

    @staticmethod
    def days_remaining(scholarship: Scholarship) -> int:
        """
        Days remaining until a scholarship's deadline, timezone-aware
        (SS9). Negative if the deadline has already passed; 0 means the
        deadline falls on today's calendar date (in the project's
        configured local timezone, Prompt 1's TIME_ZONE setting) even
        if a few hours remain, so DUE_TODAY urgency triggers correctly
        instead of an off-by-one from mixing "hours remaining" with
        "days remaining."

        Uses calendar-date subtraction in LOCAL time (timezone.localtime)
        rather than total_seconds()/86400 on the raw UTC delta -- the
        two can disagree by one day for a deadline that falls very
        early or very late in the local calendar day, which
        total_seconds() alone would misclassify.
        """
        now_local_date = timezone.localtime(timezone.now()).date()
        deadline_local_date = timezone.localtime(scholarship.deadline).date()
        return (deadline_local_date - now_local_date).days

    @staticmethod
    def urgency(scholarship: Scholarship) -> str:
        """Map days_remaining to one DeadlineUrgency category. See
        module docstring "URGENCY THRESHOLDS" for the exact rule."""
        days = DeadlineService.days_remaining(scholarship)

        if scholarship.is_expired:
            return DeadlineUrgency.EXPIRED
        if days <= 0:
            return DeadlineUrgency.DUE_TODAY
        if days <= _CRITICAL_MAX_DAYS:
            return DeadlineUrgency.CRITICAL
        if days <= _URGENT_MAX_DAYS:
            return DeadlineUrgency.URGENT
        if days <= _APPROACHING_MAX_DAYS:
            return DeadlineUrgency.APPROACHING
        return DeadlineUrgency.UPCOMING

    @staticmethod
    def weekly_priority_list(student: StudentProfile):
        """
        Combine deadline urgency, match score, and application
        readiness into a short prioritized list (FR-14), e.g. "3
        scholarships you should prioritize this week." Reuses
        top_opportunities() (below) and filters to CRITICAL/URGENT/
        DUE_TODAY entries -- FR-14's own examples all describe
        near-term deadlines (2/7/20 days), matching this module's
        URGENT threshold at 7 days.
        """
        opportunities = DeadlineService.top_opportunities(student, limit=None)
        return [
            o for o in opportunities
            if o.urgency in (DeadlineUrgency.DUE_TODAY, DeadlineUrgency.CRITICAL, DeadlineUrgency.URGENT)
        ]

    @staticmethod
    def top_opportunities(student: StudentProfile, limit: int = 5):
        """
        Compose Match Score + Eligibility + Readiness + Deadline + one
        Recommended Action per entry (FR-20) -- the successor to the
        v3.0 Top-5 Plan.

        PERFORMANCE NOTE (SS18): candidates are fetched in one query
        (select_related('provider') + prefetch_related('criteria__weight')),
        avoiding N+1 on the candidate list itself. Each candidate then
        gets one compute_match_score + one evaluate + one
        compute_readiness call, each of which internally does its own
        single select_related('weight') fetch of that scholarship's own
        criteria (Prompt 4's existing "no N+1" design) -- i.e. a
        constant, small number of queries per scholarship, not per
        criterion, so total query count scales with the number of
        candidate scholarships, not with scholarships x criteria. No
        further caching/batching is introduced in this prompt beyond
        what Prompt 4 already established, matching that module's own
        documented tradeoff (real complexity not worth paying for at
        this data scale -- SS18/SS20 "avoid... unnecessary complexity").

        RANKING (FR-20 + FR-14): scholarships are split into two
        eligibility tiers -- ELIGIBLE and "near-eligible" (FR-20's own
        term: "the student does not currently satisfy one or more
        remediable requirements but has a meaningful match score and a
        clearly identifiable path toward eligibility"). This service
        interprets "remediable" as: no MANDATORY criterion is
        MISSING_INFO-with-no-possible-remedy or structurally impossible
        -- concretely, near-eligible means overall status is
        NOT_ELIGIBLE or MISSING_INFO (not ELIGIBLE) while the Match
        Score is still > 0 (i.e. at least one criterion is satisfied,
        so there IS a quantifiable "meaningful match" per FR-20's own
        phrase, rather than a scholarship the student matches on
        nothing at all). A Not/Missing-eligible scholarship with a
        Match Score of exactly 0 is excluded entirely -- there is no
        SRS basis to call a zero-match scholarship an "opportunity."

        Within each tier, entries are ordered by Match Score
        descending, then by days_remaining ascending (soonest deadline
        first) as the tie-break -- FR-14 explicitly names deadline
        urgency as one of the three combined factors ("combining
        deadline urgency, match score, and application readiness"),
        and Match Score is the primary FR-20 ranking signal (its
        worked table SS4.4 is sorted by Match Score, #1 92% down to
        #5 70%). Readiness is surfaced on every entry (SS12: "Match
        Score, Eligibility status, Readiness percentage...") but is
        NOT used as a sort key -- the SRS's own worked FR-20 table is
        sorted purely by Match Score with Readiness shown alongside,
        not blended into ranking, so this service does not invent a
        combined score (SS10: "do NOT blindly combine them unless
        supported by the SRS").

        Eligible tier always ranks above near-eligible tier, matching
        FR-20's own ordering intent (its example table's #1-#2 are
        Eligible, #3-#4 are Not Eligible-with-a-remedy, #5 is Eligible
        again but lower-scored -- i.e. eligibility is not a strict
        pre-sort in the SRS's own example either; however #5 at 70% has
        no gap at all, so the SRS's example is consistent with sorting
        primarily by Match Score while still never letting a genuinely
        Not-Eligible-with-zero-remedy entry outrank a real opportunity
        -- this service's two-tier-then-score-sort achieves the same
        outcome without contradicting the worked example, since in that
        example every entry the SRS chose to show already has a
        meaningful match score).

        ``limit=None`` returns every currently visible, scoreable
        scholarship (used by weekly_priority_list, which does its own
        filtering afterward); ``limit=5`` (the FR-20 default, "typically
        five") truncates after ranking.
        """
        candidates = Scholarship.objects.filter(
            is_published=True, is_active=True,
        ).select_related("provider").prefetch_related("criteria__weight")

        eligible_tier = []
        near_eligible_tier = []

        for scholarship in candidates:
            if scholarship.is_expired:
                continue
            try:
                _validate_scholarship_recommendable(scholarship)
            except ValidationError:
                continue

            recommendation = RecommendationService.compute_match_score(student, scholarship)
            eligibility_results = EligibilityService.evaluate(student, scholarship)
            overall_status = EligibilityService.rollup_status(eligibility_results)
            readiness = ReadinessService.compute_readiness(student, scholarship)

            if overall_status == EligibilityStatus.ELIGIBLE:
                is_near_eligible = False
            else:
                if recommendation.match_score_percent <= 0:
                    continue  # no meaningful match at all -- not an opportunity (FR-20)
                is_near_eligible = True

            days = DeadlineService.days_remaining(scholarship)
            urgency = DeadlineService.urgency(scholarship)
            action = DeadlineService._recommended_action(
                overall_status, readiness, eligibility_results,
            )

            entry = OpportunityEntry(
                scholarship=scholarship,
                match_score_percent=recommendation.match_score_percent,
                eligibility_status=overall_status,
                is_near_eligible=is_near_eligible,
                readiness_percent=readiness.readiness_percent,
                days_remaining=days,
                urgency=urgency,
                recommended_action=action,
            )
            (near_eligible_tier if is_near_eligible else eligible_tier).append(entry)

        def _sort_key(entry):
            return (-entry.match_score_percent, entry.days_remaining)

        eligible_tier.sort(key=_sort_key)
        near_eligible_tier.sort(key=_sort_key)

        ranked = eligible_tier + near_eligible_tier
        if limit is not None:
            ranked = ranked[:limit]
        return ranked

    @staticmethod
    def _recommended_action(overall_status: str, readiness, eligibility_results) -> str:
        """
        One factual recommended action per FR-20's worked table style
        ("Apply now", "Prepare recommendation letter", "Upload income
        certificate", "Raise CGPA by 0.3"). Derived entirely from
        already-computed facts -- the first item of the readiness
        action plan (Prompt 5, itself built from Prompt 4's gap
        explanations) when anything is missing, or a fixed "Apply now"
        when the student is both eligible and fully ready. No new
        wording logic is invented beyond what ReadinessService already
        produced; this is a selection, not a generation, step.
        """
        if overall_status == EligibilityStatus.ELIGIBLE and readiness.readiness_percent == Decimal("100.00"):
            return "Apply now"
        if readiness.action_plan:
            return readiness.action_plan[0]
        if overall_status == EligibilityStatus.MISSING_INFO:
            return "Complete your profile to determine full eligibility."
        return "Review this scholarship's requirements."
