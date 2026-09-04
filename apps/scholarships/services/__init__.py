"""
apps/scholarships/services/__init__.py

SCHOLARSHIP MANAGEMENT SERVICE LAYER (Prompt 3).

Views in this app must stay thin: parse the request, call one of these
services, render the response. All scholarship/criteria/weight
business rules live here, not in views.py or templates — same
convention apps.accounts already established in Prompt 2.

FOUR SERVICES, MATCHING THE FOUR RESPONSIBILITIES THE SRS SEPARATES:
  - ScholarshipService   — scholarship record CRUD + ownership checks (FR-04)
  - CriteriaService       — ScholarshipCriterion CRUD (FR-04, feeds FR-07 later)
  - WeightService         — ScholarshipCriterionWeight CRUD + the 100%-sum
                             validation (FR-04, FR-06)
  - PublicationService    — the publish/unpublish/deactivate gate, combining
                             WeightService's validation with the FR-03
                             verified-provider requirement (via
                             Scholarship.can_be_published())

RECOMMENDATION-ENGINE BOUNDARY (read before adding anything here):
These services provide and validate DATA. They compute nothing about a
student — no Match Score, no eligibility classification, no gap, no
readiness. Prompt 4's RecommendationService / EligibilityService (in
apps.recommendations, already stubbed since Prompt 1) are the only
things that may ever compare a criterion's required_value against a
StudentProfile's actual value. If a function in this package ever
needs to know what a *student* has, it belongs in apps.recommendations
instead — that is the line this package must never cross.
"""
