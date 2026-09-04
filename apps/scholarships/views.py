"""
apps/scholarships/views.py

Prompt 1 left a single placeholder view here. Prompt 3 replaces it with
the full scholarship-management module: provider CRUD + criteria +
weights + publish/unpublish, student discovery (list + detail), and
administrator inspection/moderation.

Views stay thin: parse the request, call apps.scholarships.services,
render. No recommendation/eligibility/Match-Score logic appears
anywhere in this file -- see services/__init__.py's "RECOMMENDATION
ENGINE BOUNDARY" note.

The single `scholarships:list` URL name from Prompt 1 is preserved
(the navbar already links to it for both students and providers) --
ScholarshipListView below branches by request.user.role internally
rather than introducing a second URL name, so no existing link breaks.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import TemplateView

from apps.common.enums import RoleChoices
from apps.common.rbac import role_required, verified_provider_required

from .forms import ScholarshipCriterionForm, ScholarshipForm
from .models import Scholarship, ScholarshipCriterion
from .services.criteria_service import CriteriaService
from .services.publication_service import PublicationService
from .services.scholarship_service import ScholarshipService
from .services.weight_service import WeightService


# ---------------------------------------------------------------------
# Shared / role-dispatching list view (preserves the Prompt 1 URL name)
# ---------------------------------------------------------------------

class ScholarshipListView(LoginRequiredMixin, TemplateView):
    """
    Dispatches by role: students get the published-scholarship catalog
    (FR-05), providers get their own management list (FR-04), admins
    get the moderation list (project brief SS13). This keeps the single
    `scholarships:list` URL name every existing template/nav link
    already points at, rather than splitting into three URL names that
    would require updating the navbar and any other referrer.
    """

    def get(self, request, *args, **kwargs):
        if request.user.role == RoleChoices.PROVIDER:
            return _provider_scholarship_list(request)
        if request.user.role == RoleChoices.ADMIN:
            return _admin_scholarship_list(request)
        return _student_scholarship_list(request)


# ---------------------------------------------------------------------
# Student discovery (FR-05) -- read-only, no Match Score / eligibility.
# ---------------------------------------------------------------------

def _student_scholarship_list(request):
    scholarships = (
        Scholarship.objects.filter(is_published=True, is_active=True)
        .select_related("provider")
        .prefetch_related("criteria__weight")
    )

    query = request.GET.get("q", "").strip()
    if query:
        scholarships = scholarships.filter(
            Q(title__icontains=query) | Q(provider__organization_name__icontains=query)
        )

    provider_id = request.GET.get("provider", "").strip()
    if provider_id.isdigit():
        scholarships = scholarships.filter(provider_id=provider_id)

    deadline_after = request.GET.get("deadline_after", "").strip()
    if deadline_after:
        scholarships = scholarships.filter(deadline__date__gte=deadline_after)

    # Exclude expired scholarships from the catalog even though they're
    # still is_published/is_active -- discovery should never surface a
    # scholarship a student can no longer apply to (is_expired is a
    # derived property, so filter in Python only on the already-paginated
    # page, keeping the DB query itself simple and indexable).
    scholarships = [s for s in scholarships.order_by("-created_at", "-id") if not s.is_expired]

    paginator = Paginator(scholarships, 12)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "scholarships/student_list.html",
        {"page_obj": page_obj, "query": query},
    )


class ScholarshipDetailView(LoginRequiredMixin, TemplateView):
    """
    Student-facing detail page (project brief SS12). Deliberately shows
    NO Match Score, eligibility result, or gap -- those are Prompt 4.
    A student may view any published+active, non-expired scholarship;
    an unpublished/inactive/expired one 404s for students (but remains
    reachable by its owning provider and by admins via their own views).
    """

    template_name = "scholarships/student_detail.html"

    def get(self, request, *args, **kwargs):
        scholarship = get_object_or_404(
            Scholarship.objects.select_related("provider").prefetch_related("criteria__weight"),
            pk=kwargs["pk"],
        )
        if request.user.role == RoleChoices.STUDENT:
            if not scholarship.is_visible_to_students:
                from django.http import Http404

                raise Http404("This scholarship is not currently available.")

        is_bookmarked = False
        if request.user.role == RoleChoices.STUDENT:
            # Local import (not module-level): apps.scholarships must
            # never depend on apps.applications at import time, since
            # apps.applications already depends on apps.scholarships
            # (a module-level import here would create a real circular
            # import). Same pattern already used in
            # apps.dashboard.views for its apps.recommendations call
            # (Prompt 5) -- by view-execution time all apps are fully
            # loaded, so this is safe.
            from apps.applications.models import Bookmark

            profile = getattr(request.user, "student_profile", None)
            if profile is not None:
                is_bookmarked = Bookmark.objects.filter(student=profile, scholarship=scholarship).exists()

        return render(request, self.template_name, {"scholarship": scholarship, "is_bookmarked": is_bookmarked})


# ---------------------------------------------------------------------
# Provider management (FR-04) -- all ownership-enforced via
# ScholarshipService.get_owned_scholarship_or_403.
# ---------------------------------------------------------------------

def _provider_scholarship_list(request):
    profile = getattr(request.user, "provider_profile", None)
    scholarships = (
        Scholarship.objects.filter(provider=profile).prefetch_related("criteria__weight")
        if profile else Scholarship.objects.none()
    )
    return render(request, "scholarships/provider_list.html", {"scholarships": scholarships, "profile": profile})


@verified_provider_required
def provider_scholarship_create(request):
    profile = request.user.provider_profile

    if request.method == "POST":
        form = ScholarshipForm(request.POST)
        if form.is_valid():
            scholarship = ScholarshipService.create_scholarship(
                provider_profile=profile,
                actor=request.user,
                title=form.cleaned_data["title"],
                description=form.cleaned_data["description"],
                amount=form.cleaned_data["amount"],
                deadline=form.cleaned_data["deadline"],
                application_instructions=form.cleaned_data["application_instructions"],
                required_documents=form.cleaned_data["required_documents"],
            )
            messages.success(request, f"Scholarship '{scholarship.title}' created as a draft.")
            return redirect("scholarships:provider_detail", pk=scholarship.pk)
    else:
        form = ScholarshipForm()

    return render(request, "scholarships/provider_form.html", {"form": form, "is_create": True})


@verified_provider_required
def provider_scholarship_edit(request, pk):
    profile = request.user.provider_profile
    scholarship = ScholarshipService.get_owned_scholarship_or_403(pk, profile)

    if request.method == "POST":
        form = ScholarshipForm(request.POST, instance=scholarship)
        if form.is_valid():
            ScholarshipService.update_scholarship(
                scholarship=scholarship,
                actor=request.user,
                title=form.cleaned_data["title"],
                description=form.cleaned_data["description"],
                amount=form.cleaned_data["amount"],
                deadline=form.cleaned_data["deadline"],
                application_instructions=form.cleaned_data["application_instructions"],
                required_documents=form.cleaned_data["required_documents"],
            )
            messages.success(request, "Scholarship updated.")
            return redirect("scholarships:provider_detail", pk=scholarship.pk)
    else:
        form = ScholarshipForm(instance=scholarship)

    return render(
        request, "scholarships/provider_form.html",
        {"form": form, "is_create": False, "scholarship": scholarship},
    )


@verified_provider_required
def provider_scholarship_detail(request, pk):
    """
    Provider's own management view of one scholarship: summary, criteria
    list, weight-validation status, and the publish/unpublish/deactivate
    actions -- the "preview before publishing" surface project brief
    SS19 asks for.
    """
    profile = request.user.provider_profile
    scholarship = ScholarshipService.get_owned_scholarship_or_403(pk, profile)
    validation = WeightService.validate_configuration(scholarship)
    return render(
        request, "scholarships/provider_detail.html",
        {"scholarship": scholarship, "validation": validation},
    )


@verified_provider_required
def provider_scholarship_delete(request, pk):
    profile = request.user.provider_profile
    scholarship = ScholarshipService.get_owned_scholarship_or_403(pk, profile)

    if request.method == "POST":
        try:
            ScholarshipService.delete_scholarship(scholarship=scholarship, actor=request.user)
        except PermissionDenied as exc:
            messages.error(request, str(exc))
            return redirect("scholarships:provider_detail", pk=scholarship.pk)
        messages.success(request, "Draft scholarship deleted.")
        return redirect("scholarships:list")

    return render(request, "scholarships/provider_confirm_delete.html", {"scholarship": scholarship})


@verified_provider_required
def provider_scholarship_publish(request, pk):
    profile = request.user.provider_profile
    scholarship = ScholarshipService.get_owned_scholarship_or_403(pk, profile)

    if request.method == "POST":
        try:
            PublicationService.publish(scholarship=scholarship, actor=request.user)
            messages.success(request, f"Scholarship '{scholarship.title}' is now published.")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, str(exc))
    return redirect("scholarships:provider_detail", pk=scholarship.pk)


@verified_provider_required
def provider_scholarship_unpublish(request, pk):
    profile = request.user.provider_profile
    scholarship = ScholarshipService.get_owned_scholarship_or_403(pk, profile)

    if request.method == "POST":
        PublicationService.unpublish(scholarship=scholarship, actor=request.user)
        messages.success(request, f"Scholarship '{scholarship.title}' has been unpublished.")
    return redirect("scholarships:provider_detail", pk=scholarship.pk)


@verified_provider_required
def provider_scholarship_deactivate(request, pk):
    profile = request.user.provider_profile
    scholarship = ScholarshipService.get_owned_scholarship_or_403(pk, profile)

    if request.method == "POST":
        new_state = not scholarship.is_active
        ScholarshipService.set_active(scholarship=scholarship, is_active=new_state, actor=request.user)
        messages.success(
            request,
            f"Scholarship '{scholarship.title}' has been "
            f"{'reactivated' if new_state else 'deactivated'}.",
        )
    return redirect("scholarships:provider_detail", pk=scholarship.pk)


# --- Criteria management (FR-04) ---

@verified_provider_required
def provider_criteria_manage(request, pk):
    profile = request.user.provider_profile
    scholarship = ScholarshipService.get_owned_scholarship_or_403(pk, profile)

    if request.method == "POST":
        form = ScholarshipCriterionForm(request.POST)
        if form.is_valid():
            try:
                CriteriaService.add_criterion(
                    scholarship=scholarship,
                    actor=request.user,
                    criterion_type=form.cleaned_data["criterion_type"],
                    comparison=form.cleaned_data["comparison"],
                    required_value=form.cleaned_data["required_value"],
                    is_mandatory=form.cleaned_data["is_mandatory"],
                )
                messages.success(request, "Criterion added.")
                return redirect("scholarships:provider_criteria", pk=scholarship.pk)
            except ValidationError as exc:
                for error in exc.messages if hasattr(exc, "messages") else [str(exc)]:
                    form.add_error(None, error)
    else:
        form = ScholarshipCriterionForm()

    criteria = scholarship.criteria.select_related("weight").all()
    return render(
        request, "scholarships/provider_criteria.html",
        {"scholarship": scholarship, "criteria": criteria, "form": form},
    )


@verified_provider_required
def provider_criterion_edit(request, pk, criterion_id):
    profile = request.user.provider_profile
    scholarship = ScholarshipService.get_owned_scholarship_or_403(pk, profile)
    criterion = get_object_or_404(ScholarshipCriterion, pk=criterion_id, scholarship=scholarship)

    if request.method == "POST":
        form = ScholarshipCriterionForm(request.POST)
        form.fields["criterion_type"].disabled = True  # type is fixed after creation
        if form.is_valid():
            try:
                CriteriaService.update_criterion(
                    criterion=criterion,
                    actor=request.user,
                    comparison=form.cleaned_data["comparison"],
                    required_value=form.cleaned_data["required_value"],
                    is_mandatory=form.cleaned_data["is_mandatory"],
                )
                messages.success(request, "Criterion updated.")
                return redirect("scholarships:provider_criteria", pk=scholarship.pk)
            except ValidationError as exc:
                for error in exc.messages if hasattr(exc, "messages") else [str(exc)]:
                    form.add_error(None, error)
    else:
        form = ScholarshipCriterionForm(initial={
            "criterion_type": criterion.criterion_type,
            "comparison": criterion.comparison,
            "required_value": criterion.required_value,
            "is_mandatory": criterion.is_mandatory,
        })
        form.fields["criterion_type"].disabled = True

    return render(
        request, "scholarships/provider_criterion_edit.html",
        {"scholarship": scholarship, "criterion": criterion, "form": form},
    )


@verified_provider_required
def provider_criterion_delete(request, pk, criterion_id):
    profile = request.user.provider_profile
    scholarship = ScholarshipService.get_owned_scholarship_or_403(pk, profile)
    criterion = get_object_or_404(ScholarshipCriterion, pk=criterion_id, scholarship=scholarship)

    if request.method == "POST":
        CriteriaService.delete_criterion(criterion=criterion, actor=request.user)
        messages.success(request, "Criterion removed.")
    return redirect("scholarships:provider_criteria", pk=scholarship.pk)


# --- Weight management (FR-04, FR-06) ---

@verified_provider_required
def provider_weights_manage(request, pk):
    """
    The weight-management UI from project brief SS8: shows every
    criterion with an input for its weight, the running total, and
    whether that total is currently valid.
    """
    profile = request.user.provider_profile
    scholarship = ScholarshipService.get_owned_scholarship_or_403(pk, profile)
    criteria = list(scholarship.criteria.select_related("weight").all())

    if request.method == "POST":
        weights_by_criterion_id = {
            str(c.pk): request.POST.get(f"weight_{c.pk}", "") for c in criteria
        }
        try:
            validation = WeightService.set_weights_bulk(
                scholarship=scholarship,
                weights_by_criterion_id=weights_by_criterion_id,
                actor=request.user,
            )
            if validation.is_valid:
                messages.success(request, "Weights saved. Total is exactly 100% — ready to publish.")
            else:
                messages.warning(request, "Weights saved, but the configuration is not yet valid: " + " ".join(validation.issues))
            return redirect("scholarships:provider_weights", pk=scholarship.pk)
        except ValidationError as exc:
            messages.error(request, str(exc))
            criteria = list(scholarship.criteria.select_related("weight").all())

    validation = WeightService.validate_configuration(scholarship)
    return render(
        request, "scholarships/provider_weights.html",
        {"scholarship": scholarship, "criteria": criteria, "validation": validation},
    )


# ---------------------------------------------------------------------
# Administrator inspection/moderation (project brief SS13).
# Administrator access is intentionally broader than the ownership
# model above -- these views use role_required(ADMIN) + a bare
# get_object_or_404, not ScholarshipService.get_owned_scholarship_or_403,
# since an admin is explicitly allowed to inspect any provider's
# scholarship (project brief: "Do not bypass the provider ownership
# model in ordinary provider views. Administrator privileges are
# intentionally broader.").
# ---------------------------------------------------------------------

def _admin_scholarship_list(request):
    scholarships = (
        Scholarship.objects.select_related("provider").prefetch_related("criteria__weight").all()
    )
    query = request.GET.get("q", "").strip()
    if query:
        scholarships = scholarships.filter(
            Q(title__icontains=query) | Q(provider__organization_name__icontains=query)
        )
    return render(request, "scholarships/admin_list.html", {"scholarships": scholarships, "query": query})


@role_required(RoleChoices.ADMIN)
def admin_scholarship_detail(request, pk):
    scholarship = get_object_or_404(
        Scholarship.objects.select_related("provider").prefetch_related("criteria__weight"), pk=pk
    )
    validation = WeightService.validate_configuration(scholarship)
    return render(
        request, "scholarships/admin_detail.html",
        {"scholarship": scholarship, "validation": validation},
    )


@role_required(RoleChoices.ADMIN)
def admin_scholarship_moderate(request, pk):
    scholarship = get_object_or_404(Scholarship, pk=pk)

    if request.method == "POST":
        new_state = not scholarship.is_active
        reason = request.POST.get("reason", "").strip()
        ScholarshipService.moderate(
            scholarship=scholarship, is_active=new_state, actor=request.user, reason=reason,
        )
        messages.success(
            request,
            f"Scholarship '{scholarship.title}' has been "
            f"{'reactivated' if new_state else 'deactivated'} by administrator action.",
        )
    return redirect("scholarships:admin_detail", pk=scholarship.pk)
