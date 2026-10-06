from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView

from bookings.selectors import visible_bookings
from conversations.selectors import visible_conversations
from core.mixins import SalesRequiredMixin
from core.selectors import paginate

from . import selectors, services
from .forms import (
    CustomerFilterForm,
    CustomerForm,
    FollowUpTaskForm,
    LeadAssignForm,
    LeadFilterForm,
    LeadForm,
    LeadNoteForm,
    LeadStatusForm,
)
from .models import Customer, Lead


def _lead_redirect(request, pk):
    """Back to the lead, unless the user just handed it to someone else."""
    if selectors.visible_leads(request.user).filter(pk=pk).exists():
        return reverse("crm:lead_detail", args=[pk])
    return reverse("crm:leads")


def _safe_next(request, fallback="crm:follow_ups"):
    """Only follow a `next` value that points back inside this site."""
    target = request.POST.get("next", "")
    if target.startswith("/") and not target.startswith("//"):
        return target
    return fallback


class CustomerListView(SalesRequiredMixin, TemplateView):
    template_name = "crm/customers.html"
    page_title = "Customer Profiles"
    page_subtitle = "Central customer records shared by chat, CRM and bookings."
    active_nav = "crm"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = CustomerFilterForm(self.request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound and form.is_valid() else {}
        queryset = selectors.list_customers(
            q=data.get("q", "") or "",
            city=data.get("city", "") or "",
            user=self.request.user,
        )
        ctx["filter_form"] = form
        ctx["page_obj"] = paginate(queryset, self.request.GET.get("page"))
        ctx["total"] = queryset.count()
        return ctx


class CustomerCreateView(SalesRequiredMixin, CreateView):
    model = Customer
    form_class = CustomerForm
    template_name = "crm/customer_form.html"
    page_title = "Add Customer"
    page_subtitle = "Create a centralised customer profile."
    active_nav = "crm"

    def get_success_url(self):
        return reverse("crm:customer_detail", args=[self.object.pk])

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"{self.object.full_name} created.")
        return response


class CustomerUpdateView(SalesRequiredMixin, UpdateView):
    model = Customer
    form_class = CustomerForm
    template_name = "crm/customer_form.html"
    page_title = "Edit Customer"
    page_subtitle = "Update contact details and preferences."
    active_nav = "crm"

    def get_queryset(self):
        return selectors.visible_customers(self.request.user)

    def get_success_url(self):
        return reverse("crm:customer_detail", args=[self.object.pk])


class CustomerDetailView(SalesRequiredMixin, DetailView):
    model = Customer
    template_name = "crm/customer_detail.html"
    context_object_name = "customer"
    active_nav = "crm"

    def get_queryset(self):
        return selectors.visible_customers(self.request.user)

    def get_page_title(self):
        return self.object.full_name

    def get_page_subtitle(self):
        return " · ".join(filter(None, [self.object.email, self.object.phone, self.object.city]))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx["leads"] = selectors.visible_leads(user).filter(customer=self.object).select_related(
            "website"
        )
        ctx["bookings"] = visible_bookings(user).filter(customer=self.object)[:20]
        ctx["conversations"] = visible_conversations(user).filter(customer=self.object)[:20]
        return ctx


class LeadListView(SalesRequiredMixin, TemplateView):
    template_name = "crm/leads.html"
    page_title = "Leads"
    page_subtitle = "Filter by source, destination, status, date and assignee."
    active_nav = "crm"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = LeadFilterForm(self.request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound and form.is_valid() else {}
        queryset = selectors.list_leads(
            q=data.get("q", "") or "",
            status=data.get("status", "") or "",
            source=data.get("source", "") or "",
            destination=data.get("destination", "") or "",
            assigned_to=data.get("assigned_to"),
            website=data.get("website"),
            created_from=data.get("created_from"),
            created_to=data.get("created_to"),
            user=self.request.user,
        )
        ctx["filter_form"] = form
        ctx["page_obj"] = paginate(queryset, self.request.GET.get("page"))
        ctx["total"] = queryset.count()
        return ctx


class LeadPipelineView(SalesRequiredMixin, TemplateView):
    template_name = "crm/pipeline.html"
    page_title = "Lead Pipeline"
    page_subtitle = "New → Qualified → Interested → Payment Pending → Converted / Follow-up / Lost."
    active_nav = "crm"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["columns"] = selectors.pipeline_columns(user=self.request.user)
        return ctx


class LeadCreateView(SalesRequiredMixin, CreateView):
    model = Lead
    form_class = LeadForm
    template_name = "crm/lead_form.html"
    page_title = "Create Lead"
    page_subtitle = "Log a lead from a call, email or walk-in."
    active_nav = "crm"

    def get_initial(self):
        initial = super().get_initial()
        customer_id = self.request.GET.get("customer")
        if customer_id:
            initial["customer"] = customer_id
        return initial

    def form_valid(self, form):
        lead = form.save(commit=False)
        services.create_lead(lead=lead, actor=self.request.user, request=self.request)
        self.object = lead
        messages.success(self.request, "Lead created.")
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse("crm:lead_detail", args=[self.object.pk])


class LeadUpdateView(SalesRequiredMixin, UpdateView):
    model = Lead
    form_class = LeadForm
    template_name = "crm/lead_form.html"
    page_title = "Edit Lead"
    page_subtitle = "Update trip details, budget and assignment."
    active_nav = "crm"

    def get_queryset(self):
        return selectors.visible_leads(self.request.user)

    def form_valid(self, form):
        lead = form.save(commit=False)
        services.update_lead(
            lead=lead,
            actor=self.request.user,
            request=self.request,
            changed_fields=form.changed_data,
        )
        self.object = lead
        messages.success(self.request, "Lead updated.")
        return redirect(_lead_redirect(self.request, lead.pk))


class LeadDetailView(SalesRequiredMixin, DetailView):
    model = Lead
    template_name = "crm/lead_detail.html"
    context_object_name = "lead"
    active_nav = "crm"

    def get_queryset(self):
        return selectors.visible_leads(self.request.user).select_related(
            "customer", "website", "assigned_to", "assigned_team"
        )

    def get_page_title(self):
        return self.object.title

    def get_page_subtitle(self):
        return f"{self.object.get_status_display()} · score {self.object.score}/100"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        lead = self.object
        ctx["notes"] = lead.notes.select_related("author")
        ctx["activities"] = lead.activities.select_related("actor")[:50]
        ctx["follow_ups"] = lead.follow_ups.select_related("assigned_to")
        ctx["bookings"] = visible_bookings(self.request.user).filter(lead=lead)
        ctx["conversations"] = visible_conversations(self.request.user).filter(lead=lead)
        ctx["note_form"] = LeadNoteForm()
        ctx["status_form"] = LeadStatusForm(initial={"status": lead.status})
        ctx["assign_form"] = LeadAssignForm(
            initial={"assigned_to": lead.assigned_to_id, "assigned_team": lead.assigned_team_id}
        )
        ctx["follow_up_form"] = FollowUpTaskForm(
            initial={"lead": lead, "assigned_to": lead.assigned_to}
        )
        return ctx


class LeadStatusUpdateView(SalesRequiredMixin, View):
    def post(self, request, pk):
        lead = get_object_or_404(selectors.visible_leads(request.user), pk=pk)
        form = LeadStatusForm(request.POST)
        if form.is_valid():
            services.change_status(
                lead=lead,
                status=form.cleaned_data["status"],
                lost_reason=form.cleaned_data.get("lost_reason", ""),
                actor=request.user,
                request=request,
            )
            messages.success(request, "Lead status updated.")
        else:
            messages.error(request, "Choose a valid status.")
        return redirect("crm:lead_detail", pk=pk)


class LeadAssignView(SalesRequiredMixin, View):
    def post(self, request, pk):
        lead = get_object_or_404(selectors.visible_leads(request.user), pk=pk)
        form = LeadAssignForm(request.POST)
        if form.is_valid():
            services.assign_lead(
                lead=lead,
                user=form.cleaned_data.get("assigned_to"),
                team=form.cleaned_data.get("assigned_team"),
                actor=request.user,
                request=request,
            )
            messages.success(request, "Assignment updated.")
        else:
            messages.error(request, "Could not update the assignment.")
        return redirect(_lead_redirect(request, pk))


class LeadNoteCreateView(SalesRequiredMixin, View):
    def post(self, request, pk):
        lead = get_object_or_404(selectors.visible_leads(request.user), pk=pk)
        form = LeadNoteForm(request.POST)
        if form.is_valid():
            services.add_note(
                lead=lead,
                body=form.cleaned_data["body"],
                author=request.user,
                is_internal=form.cleaned_data["is_internal"],
            )
            messages.success(request, "Note added.")
        else:
            messages.error(request, "The note cannot be empty.")
        return redirect("crm:lead_detail", pk=pk)


class FollowUpListView(SalesRequiredMixin, TemplateView):
    template_name = "crm/follow_ups.html"
    page_title = "Follow-up Tasks"
    page_subtitle = "Overdue, due today and upcoming reminders."
    active_nav = "crm"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        include_completed = self.request.GET.get("completed") == "1"
        ctx["buckets"] = selectors.follow_up_buckets(
            user=self.request.user, include_completed=include_completed
        )
        ctx["include_completed"] = include_completed
        ctx["form"] = FollowUpTaskForm()
        return ctx


class FollowUpCreateView(SalesRequiredMixin, View):
    def post(self, request):
        form = FollowUpTaskForm(request.POST)
        if form.is_valid() and not selectors.visible_leads(request.user).filter(
            pk=form.cleaned_data["lead"].pk
        ).exists():
            messages.error(request, "Choose one of your leads.")
        elif form.is_valid():
            services.create_follow_up(
                task=form.save(commit=False), actor=request.user, request=request
            )
            messages.success(request, "Follow-up scheduled.")
        else:
            messages.error(request, form.errors.as_text())
        return redirect(_safe_next(request))


class FollowUpCompleteView(SalesRequiredMixin, View):
    def post(self, request, pk):
        task = get_object_or_404(selectors.visible_follow_ups(request.user), pk=pk)
        services.complete_follow_up(task=task, actor=request.user, request=request)
        messages.success(request, "Follow-up marked complete.")
        return redirect(_safe_next(request))
