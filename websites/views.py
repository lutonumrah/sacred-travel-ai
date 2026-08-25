from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView

from core.mixins import ManagerRequiredMixin, PageMixin
from core.selectors import paginate

from . import selectors, services
from .forms import WebsiteAPIKeyForm, WebsiteFilterForm, WebsiteForm
from .models import Website, WebsiteAPIKey


class WebsiteListView(PageMixin, TemplateView):
    template_name = "websites/list.html"
    page_title = "Website / Brand Management"
    page_subtitle = "Register websites, brands and source identifiers."
    active_nav = "websites"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = WebsiteFilterForm(self.request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound and form.is_valid() else {}
        queryset = selectors.websites_with_counts().filter(
            pk__in=selectors.list_websites(
                q=data.get("q", ""), status=data.get("status", "")
            ).values("pk")
        )
        ctx["filter_form"] = form
        ctx["page_obj"] = paginate(queryset, self.request.GET.get("page"))
        ctx["total"] = queryset.count()
        return ctx


class WebsiteCreateView(ManagerRequiredMixin, CreateView):
    model = Website
    form_class = WebsiteForm
    template_name = "websites/form.html"
    page_title = "Add Website"
    page_subtitle = "Create a new website / brand record."
    active_nav = "websites"

    def form_valid(self, form):
        response = super().form_valid(form)
        services.record_website_saved(
            website=self.object, actor=self.request.user, request=self.request, created=True
        )
        messages.success(
            self.request, f"{self.object.name} registered — a widget key was issued."
        )
        return response

    def get_success_url(self):
        return reverse("websites:detail", args=[self.object.pk])


class WebsiteUpdateView(ManagerRequiredMixin, UpdateView):
    model = Website
    form_class = WebsiteForm
    template_name = "websites/form.html"
    page_title = "Edit Website"
    page_subtitle = "Update brand, domain and widget settings."
    active_nav = "websites"

    def get_queryset(self):
        return selectors.list_websites()

    def form_valid(self, form):
        response = super().form_valid(form)
        services.record_website_saved(
            website=self.object, actor=self.request.user, request=self.request
        )
        messages.success(self.request, "Website updated.")
        return response

    def get_success_url(self):
        return reverse("websites:detail", args=[self.object.pk])


class WebsiteDetailView(PageMixin, DetailView):
    model = Website
    template_name = "websites/detail.html"
    context_object_name = "website"
    active_nav = "websites"

    def get_queryset(self):
        return selectors.list_websites()

    def get_page_title(self):
        return self.object.name

    def get_page_subtitle(self):
        return f"{self.object.domain} · source `{self.object.source_identifier}`"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        website = self.object
        ctx["api_keys"] = website.api_keys.all()
        ctx["key_form"] = WebsiteAPIKeyForm()
        ctx["lead_count"] = website.leads.filter(is_deleted=False).count()
        ctx["conversation_count"] = website.conversations.count()
        ctx["booking_count"] = website.bookings.count()
        active_key = website.api_keys.filter(is_active=True).first()
        ctx["active_key"] = active_key
        ctx["embed_snippet"] = _embed_snippet(self.request, website, active_key)
        return ctx


def _embed_snippet(request, website, api_key):
    if not api_key:
        return "Issue an API key to generate the embed snippet."
    base = request.build_absolute_uri("/").rstrip("/")
    return (
        '<script\n'
        f'  src="{base}/static/js/widget.js"\n'
        f'  data-scared-key="{api_key.public_key}"\n'
        f'  data-scared-api="{base}"\n'
        f'  data-scared-color="{website.primary_color}"\n'
        f'  data-scared-title="{website.brand_name or website.name}"\n'
        '  defer></script>'
    )


class WebsiteKeyCreateView(ManagerRequiredMixin, View):
    def post(self, request, pk):
        website = get_object_or_404(selectors.list_websites(), pk=pk)
        form = WebsiteAPIKeyForm(request.POST)
        key_name = form.data.get("key_name") or "Widget key"
        services.issue_api_key(
            website=website, key_name=key_name, actor=request.user, request=request
        )
        messages.success(request, "New widget key issued.")
        return redirect("websites:detail", pk=pk)


class WebsiteKeyRevokeView(ManagerRequiredMixin, View):
    def post(self, request, pk, key_id):
        api_key = get_object_or_404(WebsiteAPIKey, pk=key_id, website_id=pk)
        services.revoke_api_key(api_key=api_key, actor=request.user, request=request)
        messages.success(request, "Widget key revoked.")
        return redirect("websites:detail", pk=pk)


class WebsiteDeleteView(ManagerRequiredMixin, View):
    def post(self, request, pk):
        website = get_object_or_404(selectors.list_websites(), pk=pk)
        services.soft_delete_website(website=website, actor=request.user, request=request)
        messages.success(request, f"{website.name} archived.")
        return redirect("websites:list")
