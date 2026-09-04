"""
apps/accounts/forms.py

Prompt 1 established SSRAMSLoginForm and account-only registration
forms. Prompt 2 extends registration to also create the full profile
(StudentProfile / ProviderProfile + ProviderVerification) in one
transactional save, and adds the profile-edit and admin
verification-decision forms this prompt's views need.

Nothing from Prompt 1 is removed or restructured -- StudentRegistrationForm
and ProviderRegistrationForm keep their existing fields/behavior; only
their save() methods are extended to also create the profile, and that
extension is wrapped in a transaction (project brief SS2: "Ensure profile
creation is reliable and transactional where appropriate").
"""

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.db import transaction

from apps.common.enums import ProviderVerificationStatus, RoleChoices

from .models import ProviderProfile, ProviderVerification, StudentProfile, User

_TEXT_INPUT = forms.TextInput(attrs={"class": "form-control"})


def _number_input(step):
    return forms.NumberInput(attrs={"class": "form-control", "step": step})


class SSRAMSLoginForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={"class": "form-control", "autofocus": True})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "form-control"})
    )


class StudentRegistrationForm(UserCreationForm):
    """
    Registers a User with role=STUDENT (FR-01: "students register
    directly") AND creates the accompanying StudentProfile in the same
    transaction (FR-02) -- this is the Prompt 2 completion of the
    Prompt 1 account-only form. Required StudentProfile fields are
    collected here so registration produces a usable profile immediately
    rather than an empty shell a student has to remember to fill in.
    """

    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={"class": "form-control"}))

    # --- StudentProfile fields (FR-02) ---
    university = forms.CharField(max_length=255, widget=_TEXT_INPUT)
    department = forms.CharField(max_length=255, widget=_TEXT_INPUT)
    cgpa = forms.DecimalField(
        max_digits=3, decimal_places=2, min_value=0, max_value=4,
        widget=_number_input("0.01"),
        help_text="On a 4.00 scale, e.g. 3.72.",
    )
    academic_level = forms.CharField(
        max_length=100, widget=_TEXT_INPUT,
        help_text='e.g. "Undergraduate -- 3rd Year", "Graduate".',
    )
    family_monthly_income = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0,
        widget=_number_input("0.01"),
        help_text="Monthly family income in BDT.",
    )
    location = forms.CharField(max_length=255, required=False, widget=_TEXT_INPUT)

    class Meta:
        model = User
        fields = ("username", "email", "first_name", "last_name")
        widgets = {
            "username": _TEXT_INPUT,
            "first_name": _TEXT_INPUT,
            "last_name": _TEXT_INPUT,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("password1", "password2"):
            self.fields[field_name].widget.attrs["class"] = "form-control"

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = RoleChoices.STUDENT
        if commit:
            user.save()
            StudentProfile.objects.create(
                user=user,
                university=self.cleaned_data["university"],
                department=self.cleaned_data["department"],
                cgpa=self.cleaned_data["cgpa"],
                academic_level=self.cleaned_data["academic_level"],
                family_monthly_income=self.cleaned_data["family_monthly_income"],
                location=self.cleaned_data.get("location", ""),
            )
        return user


class ProviderRegistrationForm(UserCreationForm):
    """
    Registers a User with role=PROVIDER (FR-01) AND creates
    ProviderProfile + an initial ProviderVerification row (status
    PENDING) in the same transaction (FR-03). apps.common.rbac.
    verified_provider_required already gates restricted actions on
    ProviderProfile.verification_status, which defaults to PENDING --
    this form just also opens the auditable verification-history record
    an administrator will act on.
    """

    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={"class": "form-control"}))
    organization_name = forms.CharField(max_length=255, widget=_TEXT_INPUT)
    organization_type = forms.CharField(
        max_length=100, required=False, widget=_TEXT_INPUT,
        help_text='e.g. "NGO", "Corporate Foundation", "Government Body".',
    )
    contact_phone = forms.CharField(max_length=30, required=False, widget=_TEXT_INPUT)
    website = forms.URLField(required=False, widget=forms.URLInput(attrs={"class": "form-control"}))
    description = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 3})
    )
    submitted_documents_note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        help_text="Briefly describe the verification evidence you can provide "
        "(e.g. registration certificate, tax ID). Document upload is added in a later prompt.",
    )

    class Meta:
        model = User
        fields = ("username", "email", "first_name", "last_name")
        widgets = {
            "username": _TEXT_INPUT,
            "first_name": _TEXT_INPUT,
            "last_name": _TEXT_INPUT,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("password1", "password2"):
            self.fields[field_name].widget.attrs["class"] = "form-control"

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = RoleChoices.PROVIDER
        if commit:
            user.save()
            provider_profile = ProviderProfile.objects.create(
                user=user,
                organization_name=self.cleaned_data["organization_name"],
                organization_type=self.cleaned_data.get("organization_type", ""),
                contact_email=self.cleaned_data["email"],
                contact_phone=self.cleaned_data.get("contact_phone", ""),
                website=self.cleaned_data.get("website", ""),
                description=self.cleaned_data.get("description", ""),
                verification_status=ProviderVerificationStatus.PENDING,
            )
            ProviderVerification.objects.create(
                provider_profile=provider_profile,
                submitted_documents_note=self.cleaned_data.get("submitted_documents_note", ""),
                status=ProviderVerificationStatus.PENDING,
            )
        return user


class StudentProfileForm(forms.ModelForm):
    """
    Edit form for an existing StudentProfile (FR-02). Deliberately a
    plain ModelForm over the model's editable fields -- no recommendation/
    eligibility logic here, per project brief SS5 ("Do not implement
    recommendation calculations here").
    """

    class Meta:
        model = StudentProfile
        fields = [
            "university", "department", "cgpa", "academic_level",
            "family_monthly_income", "location",
            "skills", "interests", "achievements", "extracurriculars",
        ]
        widgets = {
            "university": _TEXT_INPUT,
            "department": _TEXT_INPUT,
            "cgpa": _number_input("0.01"),
            "academic_level": _TEXT_INPUT,
            "family_monthly_income": _number_input("0.01"),
            "location": _TEXT_INPUT,
        }

    # skills/interests/achievements/extracurriculars are JSONField list
    # columns (see models.py). For this prompt's UI they're edited as a
    # simple comma-separated text line and converted to/from a JSON list
    # here, rather than building a dynamic tag-input widget -- the field
    # itself stays a list on the model either way, so a richer widget can
    # replace this later without touching StudentProfile.
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("skills", "interests", "achievements", "extracurriculars"):
            self.fields[field_name] = forms.CharField(
                required=False,
                widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Comma-separated"}),
            )
            if self.instance and self.instance.pk:
                current = getattr(self.instance, field_name) or []
                self.initial[field_name] = ", ".join(current)

    def clean_cgpa(self):
        cgpa = self.cleaned_data["cgpa"]
        if cgpa < 0 or cgpa > 4:
            raise forms.ValidationError("CGPA must be between 0.00 and 4.00.")
        return cgpa

    def clean_family_monthly_income(self):
        income = self.cleaned_data["family_monthly_income"]
        if income < 0:
            raise forms.ValidationError("Family monthly income cannot be negative.")
        return income

    def _clean_list_field(self, field_name):
        raw = self.cleaned_data.get(field_name, "")
        return [item.strip() for item in raw.split(",") if item.strip()]

    def clean_skills(self):
        return self._clean_list_field("skills")

    def clean_interests(self):
        return self._clean_list_field("interests")

    def clean_achievements(self):
        return self._clean_list_field("achievements")

    def clean_extracurriculars(self):
        return self._clean_list_field("extracurriculars")


class ProviderProfileForm(forms.ModelForm):
    """
    Edit form for an existing ProviderProfile (FR-03). Does not expose
    ``verification_status`` -- that field is only ever changed through
    the admin verification decision workflow (see
    ProviderVerificationDecisionForm), never by the provider directly,
    matching the model's own docstring ("never directly by
    provider-facing views").
    """

    class Meta:
        model = ProviderProfile
        fields = [
            "organization_name", "organization_type", "contact_email",
            "contact_phone", "website", "description",
        ]
        widgets = {
            "organization_name": _TEXT_INPUT,
            "organization_type": _TEXT_INPUT,
            "contact_email": forms.EmailInput(attrs={"class": "form-control"}),
            "contact_phone": _TEXT_INPUT,
            "website": forms.URLInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class ProviderVerificationDecisionForm(forms.Form):
    """
    Administrator decision form for one ProviderVerification submission
    (FR-03, FR-17). Only APPROVED/REJECTED are offered -- PENDING is a
    starting state, not something an admin "decides" into -- using the
    exact ProviderVerificationStatus values already defined in
    apps.common.enums (no new statuses introduced).
    """

    DECISION_CHOICES = [
        (ProviderVerificationStatus.APPROVED, "Approve"),
        (ProviderVerificationStatus.REJECTED, "Reject"),
    ]

    decision = forms.ChoiceField(
        choices=DECISION_CHOICES,
        widget=forms.RadioSelect,
    )
    decision_note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        help_text="Optional note explaining the decision (visible to the provider).",
    )
