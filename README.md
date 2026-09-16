# SSRAMS v3.1

## Smart Scholarship Recommendation & Application Management System

A Django-based scholarship management platform with student profiles, scholarship publishing, eligibility checking, recommendation, application tracking, and an AI-powered advisor.

**Institution:** IUBAT  
**Project:** SSRAMS v3.1  
**Updated:** August 2026

---

## 1. Project Overview

SSRAMS is designed to help students discover scholarships that match their academic and personal information, understand their eligibility, prepare applications, and track application progress.

The system also provides scholarship providers with tools to create and manage scholarship opportunities, define eligibility criteria, review applications, and manage their listings. Administrators can manage users, verify providers, moderate scholarships, and review system activity.

The project is built with Django and follows a service-based architecture so that business rules remain separate from the presentation layer.

### Main Features

- Student and provider registration
- Role-based access control
- Student and provider profile management
- Provider verification by administrators
- Scholarship creation and management
- Scholarship eligibility criteria and custom weights
- Match score calculation
- Eligibility evaluation
- Gap analysis
- Application readiness information
- Deadline and opportunity tracking
- Top Opportunities for students
- Scholarship applications and provider review
- Application status history
- Scholarship bookmarks
- AI-powered scholarship advisor
- AI strategy planner
- Profile improvement suggestions
- Audit logging
- Django admin support

---

## 2. Current Implementation

The main SSRAMS features are implemented across the project's Django apps.

### Accounts

The accounts module handles authentication, user roles, student profiles, provider profiles, and provider verification.

Supported roles:

- Student
- Provider
- Administrator

Students and providers complete their relevant profile information during registration. Administrators can review and approve or reject provider verification requests.

### Scholarships

Providers can create scholarships and define their requirements. Each scholarship can have its own criteria and weights.

The scholarship module supports:

- Scholarship creation and editing
- Publishing and unpublishing
- Activation and deactivation
- Eligibility criteria
- Criterion weights
- Provider ownership checks
- Administrator moderation
- Required documents
- Application instructions
- Search and pagination

A scholarship becomes available to students only when the required publication conditions are satisfied.

### Recommendations

The recommendation module evaluates a student's profile against scholarship requirements.

It provides:

- Match Score
- Eligibility status
- Criterion-level explanations
- Missing information
- Gap analysis
- Application readiness
- Deadline information
- Top Opportunities

The recommendation and eligibility calculations are handled by dedicated services rather than being placed directly inside views.

### Applications

Students can apply for available scholarships and track their applications.

Providers can review applications submitted to their scholarships.

The module also provides:

- Application status transitions
- Status history
- Provider review
- Bookmarking
- Reapplication support
- Ownership checks

### AI Advisor

The AI Advisor uses the deterministic results already produced by the application instead of calculating scholarship eligibility or match scores itself.

The AI layer includes:

- Context-aware AI Advisor
- Strategy Planner
- Profile Improvement Advisor
- Facts Bundle generation
- Gemini API integration
- Fallback responses when AI is unavailable

The Gemini API key is kept on the server and is not exposed to the frontend.

### Dashboard

Each user role has a separate dashboard with information relevant to that role.

- Students see recommendations, deadlines, applications, bookmarks, and AI features.
- Providers see their scholarships and application activity.
- Administrators see management and verification information.

### Audit

Important actions are recorded through the audit system, including authentication, profile changes, provider verification, scholarship changes, application status changes, and other administrative activities.

---

## 3. Getting Started

### Requirements

- Python 3.11+
- Django 5.2+
- Git
- A database supported by the project
- Microsoft ODBC Driver 18 for SQL Server when using SQL Server
- A Gemini API key when AI features are required

### Installation

Clone the repository and enter the project directory:

```bash
git clone <repository-url>
cd ssrams
```

Create a virtual environment:

```bash
python3 -m venv venv
```

Activate it.

**Windows:**

```bash
venv\Scripts\activate
```

**Linux/macOS:**

```bash
source venv/bin/activate
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

Create the local environment file:

```bash
cp .env.example .env
```

On Windows, copy `.env.example` to `.env` manually if the `cp` command is not available.

Update `.env` with the required settings.

Apply the migrations:

```bash
python manage.py migrate
```

Create an administrator account:

```bash
python manage.py createsuperuser
```

Run the development server:

```bash
python manage.py runserver
```

Open:

```text
http://127.0.0.1:8000/
```

---

## 4. Database Configuration

### Microsoft SQL Server

SQL Server is the intended database for the project.

The project uses the `mssql-django` backend and ODBC Driver 18 for SQL Server.

Set the following values in `.env`:

```env
DB_ENGINE=mssql

DB_NAME=your_database_name
DB_USER=your_database_user
DB_PASSWORD=your_database_password
DB_HOST=your_database_host
DB_PORT=1433

DB_DRIVER=ODBC Driver 18 for SQL Server
DB_EXTRA_PARAMS=TrustServerCertificate=yes;Encrypt=yes
```

Make sure SQL Server and the required ODBC driver are installed and accessible from the machine running the application.

### SQLite Development Fallback

For local development and testing when SQL Server is not available, the project includes a SQLite fallback:

```env
DB_ENGINE=sqlite_dev_fallback
```

This uses:

```text
dev_fallback.sqlite3
```

The SQLite option is intended mainly for development and testing. The production deployment should use a persistent database rather than relying on a temporary SQLite filesystem.

---

## 5. Environment Variables

Sensitive configuration is loaded from environment variables rather than being hard-coded.

Important variables include:

```env
DJANGO_SECRET_KEY=
DJANGO_DEBUG=
DJANGO_ALLOWED_HOSTS=
DJANGO_TIME_ZONE=

DB_ENGINE=
DB_NAME=
DB_USER=
DB_PASSWORD=
DB_HOST=
DB_PORT=
DB_DRIVER=
DB_EXTRA_PARAMS=

GEMINI_API_KEY=
GEMINI_MODEL_NAME=
GEMINI_TIMEOUT_SECONDS=
```

### Security

Do not commit a real `.env` file or any secret keys to the repository.

The repository should contain `.env.example` with placeholder values instead.

---

## 6. Project Architecture

The project separates presentation, business logic, and data access through Django apps and service modules.

### General Flow

```text
Student Profile
      |
      v
Scholarship Criteria + Weights
      |
      v
Criterion Evaluation
      |
      +-------------------+-------------------+
      |                   |                   |
      v                   v                   v
Match Score          Eligibility          Gap Analysis
      |                   |                   |
      +-------------------+-------------------+
                          |
                          v
                    Readiness
                          |
                          v
                 Deadline / Opportunities
                          |
                          v
                    AI Advisor
```

The AI layer receives prepared facts from the application. It does not directly access the database or replace the deterministic recommendation and eligibility calculations.

### Service Layer

Views are kept relatively small. They handle requests and responses while application-specific calculations and business rules are placed in service modules.

Examples include:

- `ScholarshipService`
- `CriteriaService`
- `WeightService`
- `PublicationService`
- `CriterionEvaluationService`
- `RecommendationService`
- `EligibilityService`
- `GapService`
- `ReadinessService`
- `DeadlineService`
- `ApplicationService`
- `FactsBundleService`
- `GeminiService`
- `AIAdvisorService`

---

## 7. Application Responsibilities

| App | Responsibility |
|---|---|
| `apps.common` | Shared enums, RBAC utilities, middleware, and common models |
| `apps.accounts` | Authentication, users, roles, profiles, provider verification |
| `apps.scholarships` | Scholarship management, criteria, and weights |
| `apps.recommendations` | Match scores, eligibility, gaps, readiness, deadlines, opportunities |
| `apps.applications` | Applications, status history, provider review, bookmarks |
| `apps.ai_advisor` | Facts Bundle, Gemini integration, AI advisor features |
| `apps.dashboard` | Role-based dashboard composition |
| `apps.audit` | Audit logging |

---

## 8. Database / Model Overview

The main relationships are structured around users, scholarships, recommendations, and applications.

```text
User
├── StudentProfile
│   ├── RecommendationResult
│   ├── EligibilityResult
│   ├── ReadinessResult
│   ├── Application
│   ├── Bookmark
│   └── AIInteraction
│
└── ProviderProfile
    ├── ProviderVerification
    └── Scholarship
        ├── ScholarshipCriterion
        │   └── ScholarshipCriterionWeight
        ├── RecommendationResult
        ├── EligibilityResult
        ├── ReadinessResult
        ├── Application
        └── Bookmark

AuditLog
└── Records important actions performed across the system
```

Each scholarship has its own criterion weights. This allows different scholarships to assign different importance to criteria such as CGPA, academic level, income, location, or other requirements.

---

## 9. AI Integration

The AI Advisor uses Google's Gemini API to provide guidance based on information already calculated by the system.

The process is:

```text
Student / Scholarship Data
          |
          v
Deterministic Services
          |
          v
Facts Bundle
          |
          v
Gemini API
          |
          v
AI Guidance
```

The AI service is intentionally separated from the application's database models.

Gemini receives a prepared facts bundle rather than a Django model or database query. This keeps the deterministic results under the control of the application.

### AI Features

- Ask the AI Advisor questions about available scholarship information
- Generate an application strategy
- Identify profile improvement areas
- Provide fallback information when Gemini is unavailable

AI responses should be treated as guidance. The underlying eligibility, match score, readiness, and scholarship data are determined by the application itself.

---

## 10. Security

The project includes several security measures:

- Django password hashing
- CSRF protection
- Role-based access control
- Object ownership checks
- Secure session configuration
- Secure CSRF cookies in production
- HTTP-only session cookies
- Clickjacking protection
- Content-type sniffing protection
- HSTS support in production
- Environment-based secrets
- Server-side Gemini API key handling
- Validation of redirect targets
- Audit logging for important actions

Production credentials and API keys must never be stored directly in source code.

---

## 11. Testing and Verification

The project includes tests for the main application modules.

Run the complete test suite with:

```bash
python manage.py test apps
```

Individual app tests can be run separately:

```bash
python manage.py test apps.accounts
python manage.py test apps.scholarships
python manage.py test apps.recommendations
python manage.py test apps.applications
python manage.py test apps.ai_advisor
python manage.py test apps.audit
python manage.py test apps.dashboard
```

The project has also been checked with:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
```

The recorded test run completed successfully with **302 tests passing** after the AI integration was added.

The application has been tested using the SQLite development configuration. The production SQL Server environment should still be tested separately before being used as the final production database.

---

## 12. Project Structure

```text
ssrams/
├── manage.py
├── requirements.txt
├── .env.example
├── .gitignore
├── config/
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
│
├── apps/
│   ├── common/
│   ├── accounts/
│   ├── scholarships/
│   ├── recommendations/
│   ├── applications/
│   ├── ai_advisor/
│   ├── dashboard/
│   └── audit/
│
├── templates/
│   ├── base/
│   ├── accounts/
│   ├── dashboard/
│   ├── scholarships/
│   ├── recommendations/
│   ├── applications/
│   └── ai_advisor/
│
├── static/
├── media/
└── dev_fallback.sqlite3
```

---

## 13. Deployment

The project can be deployed as a Django web service using Gunicorn.

### Build Command

```bash
pip install -r requirements.txt && python manage.py migrate
```

### Start Command

```bash
gunicorn --timeout 120 config.wsgi:application
```

For a Render deployment using the current SQLite configuration, the required environment variables include:

```env
DJANGO_SECRET_KEY=<your-secret-key>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=<your-render-domain>
DJANGO_TIME_ZONE=Asia/Dhaka
DB_ENGINE=sqlite_dev_fallback
```

For a long-term production deployment, use a persistent database such as PostgreSQL or a properly hosted SQL Server instance rather than relying on the service filesystem for SQLite data.

---

## 14. Development Notes

When changing Django models, create and apply migrations as needed:

```bash
python manage.py makemigrations
python manage.py migrate
```

Before committing changes, it is useful to run:

```bash
python manage.py check
python manage.py test apps
```

Keep feature logic inside the appropriate app and service layer. Avoid placing database queries or complex business rules directly in templates.

---

## 15. License

This project was developed as an academic software project. Add the appropriate license information here if the project is later released under an open-source or other formal license.

---

## 16. Contributors

Add project members and their roles here.

```text
Name — Role
Name — Role
Name — Role
```
