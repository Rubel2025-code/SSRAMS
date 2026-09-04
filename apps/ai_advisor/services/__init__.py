"""
apps/ai_advisor/services/__init__.py

AI SERVICE ARCHITECTURE FOUNDATION (project brief Step 8). Full Gemini
prompting/parsing logic lands in later prompts (Member 4 — see
README.md, "Prompt 7 and Prompt 8" per the brief). This package
establishes the boundary those prompts must respect.

    Database
        |
    Django business logic          (apps.recommendations, apps.applications)
        |
    Deterministic calculations     (RecommendationService, EligibilityService,
        |                            ReadinessService, DeadlineService)
    Facts Bundle                   (FactsBundleService, this package)
        |
    Gemini                         (GeminiService, this package)
        |
    Natural-language explanation/guidance

Two services live here:
  - FactsBundleService: reads already-computed RecommendationResult /
    EligibilityResult / ReadinessResult / Application rows and assembles
    them into a plain-data structure. It NEVER computes a score, gap, or
    readiness value itself -- only reads what apps.recommendations wrote.
  - GeminiService: the only code in the whole project allowed to call
    the Gemini API. It receives a Facts Bundle (plain data) and a
    task-specific prompt; it has no database handle at all, which is
    what makes "Gemini can't query the database directly" true at the
    architecture level rather than just a comment.
"""
