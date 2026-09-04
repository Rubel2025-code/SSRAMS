"""
Django settings for the SSRAMS v3.1 project.

Smart Scholarship Recommendation & Application Management System
with AI-Powered Scholarship Advisor — IUBAT, August 2026.

This file is the FOUNDATION established in Prompt 1 of the 10-prompt
implementation plan. It intentionally does not implement feature logic —
see each app's own module for that (added in later prompts).

Reference: SSRAMS SRS v3.1, §2.1.3 (Technology Stack), §2.4 (Constraints),
§3.3.2 (Design Constraints), §3.3.3 (Security).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths & environment
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from a local .env file (never committed).
# See .env.example at the project root for the full list of variables.
load_dotenv(BASE_DIR / ".env")


def env(key, default=None, required=False):
    """Read an environment variable, optionally enforcing that it is set.

    Per SRS constraint §2.4 and the project brief: DB passwords, the
    Gemini API key, the Django SECRET_KEY, and all production credentials
    must never be hard-coded. Everything sensitive is read through here.
    """
    value = os.environ.get(key, default)
    if required and (value is None or value == ""):
        raise RuntimeError(
            f"Required environment variable '{key}' is not set. "
            f"Copy .env.example to .env and fill in a value."
        )
    return value


def env_bool(key, default=False):
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(key, default=""):
    value = os.environ.get(key, default)
    return [item.strip() for item in value.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core / security
# ---------------------------------------------------------------------------
# SECURITY WARNING: keep the secret key used in production secret!
_INSECURE_DEV_SECRET_KEY = "django-insecure-dev-only-key-CHANGE-ME-see-env-example"

SECRET_KEY = env("DJANGO_SECRET_KEY", default=_INSECURE_DEV_SECRET_KEY)

DEBUG = env_bool("DJANGO_DEBUG", default=True)

# The dev fallback key above exists only so a freshly unzipped checkout can
# run `manage.py check` / `migrate` / `test` before a .env is written. It is
# refused outright once DEBUG is off, so a deployment can never silently run
# on a publicly-known SECRET_KEY (SRS §3.3.3; "never hard-code production
# secrets"). Generate one with:
#   python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
if not DEBUG and SECRET_KEY == _INSECURE_DEV_SECRET_KEY:
    raise RuntimeError(
        "DJANGO_SECRET_KEY must be set to a real, secret value when "
        "DJANGO_DEBUG=False. The built-in development key is refused in "
        "non-debug mode. See .env.example and README.md 'Deployment'."
    )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", default="127.0.0.1,localhost")

# When DEBUG is off (staging/production), Django requires ALLOWED_HOSTS to
# be explicitly set via DJANGO_ALLOWED_HOSTS — no silent wildcard fallback.
if not DEBUG and not ALLOWED_HOSTS:
    raise RuntimeError(
        "DJANGO_ALLOWED_HOSTS must be set (comma-separated) when DJANGO_DEBUG=False."
    )

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
# NOTE ON APP BOUNDARIES (see README.md "App Responsibilities" for detail):
#   accounts        -> auth, roles, Student/Provider profiles, verification
#   scholarships    -> scholarships, criteria, provider-defined weights
#   recommendations -> match scoring, eligibility, gaps, readiness,
#                       deadline prioritization, Top Opportunities
#   applications    -> applications, status lifecycle, provider review
#   ai_advisor      -> Facts Bundle, Gemini integration, Strategy Planner,
#                       Profile Improvement Advisor, AI Advisor
#   dashboard       -> role-specific dashboard composition (read-only glue;
#                       owns no business logic or models of its own)
#   audit           -> audit logging (FR-18), used by every other app
#
# "common" is a small cross-app utilities package (not a Django "app" with
# models) — RBAC decorators/mixins and shared enums live there so no other
# app has to duplicate them.
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

THIRD_PARTY_APPS = [
    # "mssql" backend is provided by the `mssql-django` package and is
    # referenced by name in DATABASES below (no separate INSTALLED_APPS
    # entry is required for it).
]

LOCAL_APPS = [
    "apps.accounts",
    "apps.scholarships",
    "apps.recommendations",
    "apps.applications",
    "apps.ai_advisor",
    "apps.dashboard",
    "apps.audit",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Project-specific: attaches request.role / request.is_verified_provider
    # style helpers used by the RBAC foundation in apps.common.
    "apps.common.middleware.RoleContextMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Project-specific: exposes nav/role helpers to every template.
                "apps.common.context_processors.role_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database — Microsoft SQL Server (SRS §2.1.3: "Database / ORM: Microsoft
# SQL Server 2019+ / Django ORM"). See README.md "Database Configuration"
# and "Known Limitations" for how to point this at a real SQL Server
# instance, and for the local-development fallback story.
# ---------------------------------------------------------------------------
DB_ENGINE = env("DB_ENGINE", default="mssql")

if DB_ENGINE == "mssql":
    DATABASES = {
        "default": {
            "ENGINE": "mssql",
            "NAME": env("DB_NAME", required=True),
            "USER": env("DB_USER", required=True),
            "PASSWORD": env("DB_PASSWORD", required=True),
            "HOST": env("DB_HOST", required=True),
            "PORT": env("DB_PORT", default="1433"),
            "OPTIONS": {
                "driver": env("DB_DRIVER", default="ODBC Driver 18 for SQL Server"),
                "extra_params": env(
                    "DB_EXTRA_PARAMS",
                    default="TrustServerCertificate=yes;Encrypt=yes",
                ),
            },
        }
    }
elif DB_ENGINE == "sqlite_dev_fallback":
    # TEMPORARY LOCAL-DEVELOPMENT FALLBACK ONLY.
    #
    # This branch exists solely so the project can `migrate` / `runserver`
    # / run tests on a machine that does not yet have SQL Server + the
    # ODBC Driver 18 for SQL Server reachable (e.g. this sandboxed build
    # environment). It is NOT the intended production or team-development
    # database and must never be relied on for real work.
    #
    # It is only ever selected by explicitly setting DB_ENGINE=sqlite_dev_fallback
    # in .env — the default (DB_ENGINE=mssql) always requires real SQL Server
    # configuration and will raise a clear error if it is missing. See
    # README.md "Database Configuration" for the full explanation and for
    # exactly what each team member needs to install/configure.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "dev_fallback.sqlite3",
        }
    }
else:
    raise RuntimeError(
        f"Unsupported DB_ENGINE '{DB_ENGINE}'. Use 'mssql' (production/team "
        f"default) or 'sqlite_dev_fallback' (temporary local-only)."
    )

# ---------------------------------------------------------------------------
# Custom user model (SRS §2.1: three roles — Student, Scholarship Provider,
# Administrator — sharing one unified authentication architecture; see
# apps/accounts/models.py for the Role choices and profile split).
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"  # SRS §3.4: interface language is English in v3.1
TIME_ZONE = env("DJANGO_TIME_ZONE", default="Asia/Dhaka")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & media files
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Security (SRS §3.3.3 "Security"; project brief Step 12)
# ---------------------------------------------------------------------------
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = int(env("SESSION_COOKIE_AGE_SECONDS", default="28800"))  # 8h
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool("SESSION_EXPIRE_AT_BROWSER_CLOSE", default=False)
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
# NOTE: SECURE_BROWSER_XSS_FILTER (X-XSS-Protection) is deliberately NOT set.
# Django removed support for it (the header is ignored by every current
# browser and was itself a source of vulnerabilities), so on Django 5.x the
# setting is dead weight that reads like protection without providing any.
# The real protections are Django's template auto-escaping plus
# SECURE_CONTENT_TYPE_NOSNIFF and X_FRAME_OPTIONS above.

# Django 4.0+ checks the Origin header on every unsafe request when the
# request arrives over HTTPS, so the deployment origins must be declared
# explicitly or every POST behind TLS fails CSRF validation. Comma-separated,
# each entry scheme-qualified, e.g.
#   DJANGO_CSRF_TRUSTED_ORIGINS=https://ssrams.iubat.edu,https://www.ssrams.iubat.edu
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
if not DEBUG and not CSRF_TRUSTED_ORIGINS:
    # Fall back to the declared hosts rather than leaving the list empty:
    # wildcards and bare IPs are skipped because they are not valid origins.
    CSRF_TRUSTED_ORIGINS = [
        f"https://{host}" for host in ALLOWED_HOSTS if host not in ("*", "") and not host.startswith(".")
    ]

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
    SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", default="31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# ---------------------------------------------------------------------------
# Gemini API (SRS §2.1.1, FR-10/FR-19/FR-22/FR-23) — server-side only.
# The key is read from the environment here and nowhere else; it must
# never be passed to a template or exposed to the frontend. The calls
# themselves live in apps/ai_advisor/services/gemini_service.py, which is
# the only module in the project that imports the Gemini SDK.
#
# GEMINI_MODEL_NAME must name a model the key can actually reach: the
# Gemini catalog lists models that a given key/tier is not entitled to,
# and asking for one of those fails at request time (404 NOT_FOUND for an
# unavailable model, 429 RESOURCE_EXHAUSTED / 503 UNAVAILABLE for one
# that is listed but out of quota) — not at startup. Verify a change with
# `python manage.py shell -c "..."` or the smoke check in README §11a
# before relying on it. Leaving GEMINI_API_KEY blank disables AI
# entirely; every deterministic feature keeps working (SRS §2.5).
# ---------------------------------------------------------------------------
GEMINI_API_KEY = env("GEMINI_API_KEY", default="").strip()
GEMINI_MODEL_NAME = env("GEMINI_MODEL_NAME", default="gemini-3.6-flash").strip()

# .strip() above matters: a GEMINI_API_KEY of "  " would otherwise be
# truthy here, flipping AI "on" and turning every advisory request into an
# authentication failure instead of the intended graceful fallback.
GEMINI_ENABLED = bool(GEMINI_API_KEY)

# Hard bound on a single Gemini request, in seconds. These calls happen
# inside a synchronous request/response cycle and a genuine answer has
# been measured at 60s+, so an unbounded call can pin a worker
# indefinitely. On timeout the SDK raises, GeminiService converts it to
# GeminiServiceError, and the caller serves its deterministic fallback.
#
# Keep any WSGI server's own timeout ABOVE this value (e.g.
# `gunicorn --timeout 120`), or the worker is killed before the graceful
# fallback can run — gunicorn's default is only 30s.
GEMINI_TIMEOUT_SECONDS = int(env("GEMINI_TIMEOUT_SECONDS", default="90"))

# ---------------------------------------------------------------------------
# Logging (minimal foundation; audit-specific logging lives in apps.audit)
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": env("DJANGO_LOG_LEVEL", default="INFO"),
    },
}

# ---------------------------------------------------------------------------
# Messages framework -> Bootstrap 5 alert classes (used by base template)
# ---------------------------------------------------------------------------
from django.contrib.messages import constants as message_constants  # noqa: E402

MESSAGE_TAGS = {
    message_constants.DEBUG: "secondary",
    message_constants.INFO: "info",
    message_constants.SUCCESS: "success",
    message_constants.WARNING: "warning",
    message_constants.ERROR: "danger",
}
