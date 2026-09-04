"""
apps/scholarships/forms.py

Form-layer validation only (empty-field/date/decimal format checks).
Business rules (ownership, the 100%-sum check, the publish gate) live
in apps/scholarships/services/ and are NOT duplicated here -- forms
call into the service layer's exceptions where a check is genuinely a
service-layer concern (e.g. WeightService.to_decimal), rather than
re-implementing it inline.
"""

from django import forms
from django.utils import timezone

from .models import Scholarship, ScholarshipCriterion

_TEXT_INPUT = forms.TextInput(attrs={"class": "form-control"})


class ScholarshipForm(forms.ModelForm):
    """Create/edit a Scholarship's own descriptive fields (FR-04)."""

    required_documents = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Comma-separated, e.g. Recommendation Letter, Income Certificate"}),
        help_text="Comma-separated list of documents an applicant must submit.",
    )

    class Meta:
        model = Scholarship
        fields = ["title", "description", "amount", "deadline", "application_instructions"]
        widgets = {
            "title": _TEXT_INPUT,
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "deadline": forms.DateTimeInput(
                attrs={"class": "form-control", "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "application_instructions": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["deadline"].input_formats = ["%Y-%m-%dT%H:%M"]
        if self.instance and self.instance.pk:
            self.initial["required_documents"] = ", ".join(self.instance.required_documents or [])

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title:
            raise forms.ValidationError("Title cannot be empty.")
        return title

    def clean_description(self):
        description = self.cleaned_data["description"].strip()
        if not description:
            raise forms.ValidationError("Description cannot be empty.")
        return description

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount is None or amount <= 0:
            raise forms.ValidationError("Amount must be a positive number.")
        return amount

    def clean_deadline(self):
        deadline = self.cleaned_data["deadline"]
        if deadline is None:
            raise forms.ValidationError("Deadline is required.")
        if deadline <= timezone.now():
            raise forms.ValidationError("Deadline must be in the future.")
        return deadline

    def clean_required_documents(self):
        raw = self.cleaned_data.get("required_documents", "")
        return [item.strip() for item in raw.split(",") if item.strip()]

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.required_documents = self.cleaned_data["required_documents"]
        if commit:
            instance.save()
        return instance


class ScholarshipCriterionForm(forms.Form):
    """
    Create/edit ONE ScholarshipCriterion. A plain Form (not a ModelForm)
    because CriteriaService.add_criterion/update_criterion own the
    actual model-write + validation + audit logging -- this form only
    handles HTML-input-level parsing and required-field checks, then
    hands clean values to the service.
    """

    criterion_type = forms.ChoiceField(
        choices=ScholarshipCriterion.CriterionType.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    comparison = forms.ChoiceField(
        choices=ScholarshipCriterion.Comparison.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    required_value = forms.CharField(
        max_length=255,
        widget=_TEXT_INPUT,
        help_text='e.g. "3.50" for CGPA, "CSE" for Department, "Recommendation Letter, Income Certificate" for Documents.',
    )
    is_mandatory = forms.BooleanField(
        required=False, initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        help_text="If unchecked, failing this criterion does not make a student hard-ineligible.",
    )

    def clean_required_value(self):
        value = self.cleaned_data["required_value"].strip()
        if not value:
            raise forms.ValidationError("Required value cannot be empty.")
        return value
