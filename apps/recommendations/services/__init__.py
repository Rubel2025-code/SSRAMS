"""
apps/recommendations/services/__init__.py

SERVICE ARCHITECTURE FOUNDATION (project brief Step 7).

This prompt establishes the *interfaces* below so later prompts (and
other team members) implement against a stable contract instead of
putting business logic in views. None of these are fully implemented
yet — each raises NotImplementedError with a pointer to which prompt
owns it. Do not put calculation logic in views.py or templates; add it
here.

Why services, not fat models or fat views:
  - Views stay thin (HTTP concerns only): parse request -> call a
    service -> render/return.
  - apps.ai_advisor's Facts Bundle builder (later prompt) calls these
    same services rather than re-deriving scores/gaps/readiness itself,
    which is what keeps Gemini's inputs traceable to one deterministic
    source (SRS §2.1.1, FR-19).
  - Each service maps 1:1 to a pipeline stage from the project brief's
    dependency chain:

        Student Profile
              |
        Scholarship Criteria + Weights
              |
        RecommendationService        -> FR-06
              |
        EligibilityService           -> FR-07
              |
        ReadinessService              -> FR-08
              |
        DeadlineService                -> FR-14
              |
        (apps.recommendations also composes Top Opportunities, FR-20,
         from the four services above)
              |
        FactsBundleService  (apps.ai_advisor)   -> SRS §2.1.1
              |
        AIAdvisorService     (apps.ai_advisor)   -> FR-10/19/22/23
"""
