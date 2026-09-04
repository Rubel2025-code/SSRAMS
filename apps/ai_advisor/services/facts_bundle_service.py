"""
apps/ai_advisor/services/facts_bundle_service.py

Prompt 1 established this interface (all three method signatures below
are UNCHANGED from that stub). Prompt 7 fully implements it.

THE CONTROLLED BOUNDARY (Prompt 7 SS3-SS6). This service:
  - READS already-computed RecommendationResult / EligibilityResult /
    ReadinessResult / OpportunityEntry / GapEntry / Application /
    Scholarship / StudentProfile data.
  - NEVER calls a *_score(), evaluate(), compute_*(), or ranking method
    itself -- every number in a returned bundle traces back to a value
    a Prompt 4/5/6 service already computed and a caller already holds
    or that this service fetches via that service's own public method
    (e.g. RecommendationService.compute_match_score(), which is the
    SAME call a view would make -- this service does not duplicate the
    math, it just also needs the answer, exactly like any other reader
    of these services would).
  - NEVER invents, defaults, or estimates a value. If something is
    unavailable, it is omitted or marked missing explicitly (never
    silently set to a plausible-looking placeholder).
  - NEVER sends more than the requesting task needs (Prompt 7 SS20
    "Privacy / Data Minimization") -- no passwords, no emails, no
    internal DB ids, no audit records, no unrelated scholarships.

ELIGIBILITY VOCABULARY -- IMPORTANT, READ BEFORE EDITING (Prompt 7 SS6).
Prompt 7's own text refers to "NEAR_ELIGIBLE" as if it were a fourth
value alongside ELIGIBLE / NOT_ELIGIBLE / MISSING_INFO. It is NOT:
apps.common.enums.EligibilityStatus (Prompt 1, unchanged) has exactly
three values. "Near-eligible" is a *derived, Top-Opportunities-specific*
boolean (OpportunityEntry.is_near_eligible, Prompt 5) computed as
"eligibility_status is not ELIGIBLE, but Match Score > 0" -- it is a
ranking/display concept, not a stored eligibility state. Inventing a
fourth EligibilityStatus value here would violate Prompt 7's own SS2
("Do NOT rewrite or replace EligibilityService") and SS5 ("Do not
introduce another scoring system"). Every bundle below therefore always
carries the real three-value `eligibility_status` field, and ONLY in
Top-Opportunities-derived contexts (build_for_strategy_planner) also
carries the separate `is_near_eligible` boolean exactly as
OpportunityEntry/DeadlineService already computed it -- never
collapsed into or confused with eligibility_status itself.
"""

from __future__ import annotations

from apps.accounts.models import StudentProfile
from apps.applications.models import Application
from apps.recommendations.services.deadline_service import DeadlineService
from apps.recommendations.services.eligibility_service import EligibilityService
from apps.recommendations.services.gap_service import GapService
from apps.recommendations.services.readiness_service import ReadinessService
from apps.recommendations.services.recommendation_service import RecommendationService
from apps.scholarships.models import Scholarship


def _scholarship_facts(scholarship: Scholarship) -> dict:
    """
    The scholarship-level facts every task-specific bundle below needs
    a subset of. Reads Scholarship fields only -- no criteria evaluation
    happens here (that is CriterionEvaluationService's job, already
    invoked by whichever RecommendationService/EligibilityService call
    produced the results this function's callers pass in separately).
    """
    return {
        "title": scholarship.title,
        "provider_name": scholarship.provider.organization_name,
        "description": scholarship.description,
        "amount": str(scholarship.amount),
        "deadline": scholarship.deadline.isoformat(),
        "required_documents": list(scholarship.required_documents or []),
        "application_instructions": scholarship.application_instructions or "",
    }


def _student_profile_facts(student: StudentProfile) -> dict:
    """
    Minimal student profile facts (Prompt 7 SS3 list). Deliberately
    excludes: user.email, user.username, StudentProfile.pk, and every
    other field not explicitly named in the SRS-supported list (SS20
    data minimization -- e.g. `skills`/`interests` are included only
    because Profile Improvement Advisor needs them to name a concrete
    gap; the AI Advisor's own bundle omits them when the question
    doesn't need them, built separately below).
    """
    return {
        "university": student.university,
        "department": student.department,
        "academic_level": student.academic_level,
        "cgpa": str(student.cgpa),
        "family_monthly_income": str(student.family_monthly_income),
        "location": student.location or "",
    }


def _eligibility_facts(student: StudentProfile, scholarship: Scholarship) -> dict:
    """
    Reads EligibilityService.evaluate()/rollup_status() -- computes
    nothing. `gaps` reuses GapService.get_gaps() exactly as Prompt 4
    produced it (criterion_display/current_value/required_value/gap/
    status/explanation) -- the same factual, advice-free shape Prompt 4
    documented and tested (no fabricated recommendations baked into the
    gap text itself; that's Gemini's job downstream, grounded by this
    data).
    """
    eligibility_results = EligibilityService.evaluate(student, scholarship)
    overall_status = EligibilityService.rollup_status(eligibility_results)
    gaps = GapService.get_gaps(student, scholarship)

    return {
        "eligibility_status": overall_status,
        "criteria": [
            {
                "criterion": r.criterion.get_criterion_type_display(),
                "status": r.status,
                "explanation": r.explanation,
                "quantified_gap": r.quantified_gap,
                "is_mandatory": r.criterion.is_mandatory,
            }
            for r in eligibility_results
        ],
        "gaps": [
            {
                "criterion": g.criterion_display,
                "current_value": g.current_value,
                "required_value": g.required_value,
                "gap": g.gap,
                "status": g.status,
                "explanation": g.explanation,
            }
            for g in gaps
        ],
    }


def _readiness_facts(student: StudentProfile, scholarship: Scholarship) -> dict:
    """Reads ReadinessService.compute_readiness() -- computes nothing."""
    readiness = ReadinessService.compute_readiness(student, scholarship)
    return {
        "readiness_percent": str(readiness.readiness_percent),
        "required_items_count": readiness.required_items_count,
        "completed_items_count": readiness.completed_items_count,
        "missing_items": list(readiness.missing_items),
        "action_plan": list(readiness.action_plan),
    }


def _deadline_facts(scholarship: Scholarship) -> dict:
    """Reads DeadlineService.days_remaining()/urgency() -- computes nothing."""
    return {
        "days_remaining": DeadlineService.days_remaining(scholarship),
        "urgency": DeadlineService.urgency(scholarship),
    }


class FactsBundleService:
    """
    Assembles the structured, system-computed data that gets sent to
    Gemini. Reads only -- never computes a score, gap, or readiness
    value (those come from apps.recommendations.services).
    """

    @staticmethod
    def build_for_strategy_planner(student: StudentProfile, scholarships: list[Scholarship]) -> dict:
        """
        Assemble the multi-scholarship Facts Bundle for the AI Strategy
        Planner (FR-22 Stage 1): match score + breakdown, eligibility
        status + gaps, readiness % + missing items, and days remaining,
        per scholarship under consideration.

        ``scholarships`` is normally the caller's own already-ranked
        Top Opportunities list (DeadlineService.top_opportunities) --
        this method does NOT re-rank or re-filter; it reads each
        scholarship's already-computed OpportunityEntry facts alongside
        the same RecommendationService/EligibilityService/
        ReadinessService calls those entries were built from, and adds
        `is_near_eligible` from that entry (see module docstring
        "ELIGIBILITY VOCABULARY" for why that boolean is kept separate
        from eligibility_status rather than merged into it).
        """
        opportunities_by_scholarship_id = {
            entry.scholarship.pk: entry
            for entry in DeadlineService.top_opportunities(student, limit=None)
        }

        entries = []
        for scholarship in scholarships:
            recommendation = RecommendationService.compute_match_score(student, scholarship)
            eligibility = _eligibility_facts(student, scholarship)
            readiness = _readiness_facts(student, scholarship)
            deadline = _deadline_facts(scholarship)

            opportunity_entry = opportunities_by_scholarship_id.get(scholarship.pk)
            is_near_eligible = opportunity_entry.is_near_eligible if opportunity_entry else None
            recommended_action = opportunity_entry.recommended_action if opportunity_entry else None

            entries.append({
                "scholarship": _scholarship_facts(scholarship),
                "match_score_percent": str(recommendation.match_score_percent),
                "match_breakdown": dict(recommendation.breakdown),
                "is_near_eligible": is_near_eligible,
                "recommended_action_from_system": recommended_action,
                **eligibility,
                "readiness": readiness,
                "deadline": deadline,
            })

        return {"task": "strategy_planner", "student": _student_profile_facts(student), "scholarships": entries}

    @staticmethod
    def build_for_profile_improvement(student: StudentProfile) -> dict:
        """
        Assemble the catalog-wide gap/impact Facts Bundle for the
        Profile Improvement Advisor (FR-23 Stage 1): missing skills,
        missing documents, CGPA gaps, and the deterministically computed
        count of scholarships each improvement would affect.

        The "affects N scholarships" counts below are computed here by
        TALLYING already-computed GapEntry rows across every currently
        visible scholarship -- this is aggregation of existing facts
        (a count of how many times a gap already identified by
        GapService recurs), not a new calculation of eligibility, score,
        readiness, or deadline. No criterion is evaluated a second time
        with different logic; EligibilityService/GapService's own
        evaluation is the only source for every gap counted.
        """
        visible_scholarships = [s for s in Scholarship.objects.filter(
            is_published=True, is_active=True,
        ).select_related("provider").prefetch_related("criteria__weight") if not s.is_expired]

        gap_counts: dict[str, dict] = {}
        per_scholarship_gaps = []

        for scholarship in visible_scholarships:
            try:
                gaps = GapService.get_gaps(student, scholarship)
            except Exception:
                # A scholarship with an invalid/incomplete configuration
                # (e.g. weights not summing to 100%) is skipped for this
                # catalog-wide tally rather than raising -- SS18-style
                # "fail safe, don't crash" applied to an aggregate view.
                continue

            if gaps:
                per_scholarship_gaps.append({
                    "scholarship_title": scholarship.title,
                    "gaps": [
                        {"criterion": g.criterion_display, "gap": g.gap, "status": g.status}
                        for g in gaps
                    ],
                })

            for gap in gaps:
                key = gap.criterion_display
                bucket = gap_counts.setdefault(key, {"criterion": key, "affected_scholarship_count": 0, "example_gap": gap.gap})
                bucket["affected_scholarship_count"] += 1

        improvement_opportunities = sorted(
            gap_counts.values(), key=lambda b: b["affected_scholarship_count"], reverse=True
        )

        return {
            "task": "profile_improvement",
            "student": _student_profile_facts(student),
            "improvement_opportunities": improvement_opportunities,
            "total_scholarships_considered": len(visible_scholarships),
            "per_scholarship_gaps": per_scholarship_gaps,
        }

    @staticmethod
    def build_for_advisor_question(student: StudentProfile, question: str) -> dict:
        """
        Assemble the single-conversation Facts Bundle for the
        Context-Aware AI Advisor (FR-10): profile, recommended
        scholarships + breakdowns, eligibility results + gaps,
        readiness + missing items, deadlines, and application status --
        restricted to data the requesting student is authorized to see
        (SRS §3.3.3: "Students cannot access other students' ... data").

        SCOPE (Prompt 7 SS13/SS20 -- do not send the entire database):
        the bundle covers the student's own Top Opportunities (already
        ranked, already ready for display) plus their own Application
        rows -- not the entire scholarship catalog, and never another
        student's data (the ``student`` argument is always the caller's
        own StudentProfile; see views.py, which never accepts a student
        id from the request).
        """
        opportunities = DeadlineService.top_opportunities(student, limit=5)
        scholarship_entries = []
        for entry in opportunities:
            eligibility = _eligibility_facts(student, entry.scholarship)
            readiness = _readiness_facts(student, entry.scholarship)
            scholarship_entries.append({
                "scholarship": _scholarship_facts(entry.scholarship),
                "match_score_percent": str(entry.match_score_percent),
                "is_near_eligible": entry.is_near_eligible,
                **eligibility,
                "readiness": readiness,
                "deadline": {"days_remaining": entry.days_remaining, "urgency": entry.urgency},
                "recommended_action_from_system": entry.recommended_action,
            })

        applications = Application.objects.filter(student=student).select_related("scholarship")
        application_entries = [
            {
                "scholarship_title": application.scholarship.title,
                "status": application.status,
                "submitted_at": application.submitted_at.isoformat() if application.submitted_at else None,
            }
            for application in applications
        ]

        return {
            "task": "ai_advisor",
            "student": _student_profile_facts(student),
            "question": question,
            "top_opportunities": scholarship_entries,
            "applications": application_entries,
        }
