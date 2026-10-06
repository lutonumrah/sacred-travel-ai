from django.contrib import messages as django_messages
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import DetailView, FormView, TemplateView

from core.mixins import AdminRequiredMixin, PageMixin
from core.selectors import paginate
from core.services import log_audit

from . import ai, selectors, services
from .forms import AgentReplyForm, AISettingsForm, HandoffForm, InboxFilterForm
from .models import AISettings, Conversation, ConversationStatus


class InboxView(PageMixin, TemplateView):
    template_name = "conversations/inbox.html"
    page_title = "Live Conversation Inbox"
    page_subtitle = "AI and human chats currently in progress."
    active_nav = "conversations"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = InboxFilterForm(self.request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound and form.is_valid() else {}
        queryset = selectors.list_conversations(
            q=data.get("q", "") or "",
            status=data.get("status", "") or "",
            website=data.get("website"),
            user=self.request.user,
        ).exclude(status=ConversationStatus.CLOSED)
        ctx["filter_form"] = form
        ctx["page_obj"] = paginate(queryset, self.request.GET.get("page"))
        ctx["waiting"] = selectors.waiting_count()
        ctx["total"] = queryset.count()
        return ctx


class ConversationHistoryView(PageMixin, TemplateView):
    template_name = "conversations/history.html"
    page_title = "Conversation History"
    page_subtitle = "Every chat, including closed ones."
    active_nav = "conversations"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = InboxFilterForm(self.request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound and form.is_valid() else {}
        queryset = selectors.list_conversations(
            q=data.get("q", "") or "",
            status=data.get("status", "") or "",
            website=data.get("website"),
            user=self.request.user,
        )
        ctx["filter_form"] = form
        ctx["page_obj"] = paginate(queryset, self.request.GET.get("page"))
        ctx["total"] = queryset.count()
        return ctx


class ConversationDetailView(PageMixin, DetailView):
    model = Conversation
    template_name = "conversations/detail.html"
    context_object_name = "conversation"
    active_nav = "conversations"

    def get_queryset(self):
        return Conversation.objects.select_related(
            "website", "customer", "lead", "assigned_to"
        )

    def get_page_title(self):
        customer = self.object.customer
        return customer.full_name if customer else f"Chat {self.object.session_key[:8]}"

    def get_page_subtitle(self):
        website = self.object.website
        source = website.source_identifier if website else "unknown source"
        return f"{self.object.get_status_display()} · {source}"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        conversation = self.object
        ctx["messages_list"] = selectors.conversation_messages(conversation)
        ctx["recommendations"] = conversation.recommendations.all()[:20]
        ctx["handoffs"] = conversation.handoffs.select_related("taken_by")
        ctx["reply_form"] = AgentReplyForm()
        ctx["handoff_form"] = HandoffForm()
        ctx["requirements"] = conversation.requirements or {}
        ctx["contact"] = conversation.context or {}
        ctx["can_reply"] = conversation.status != ConversationStatus.CLOSED
        return ctx


class ConversationReplyView(PageMixin, View):
    def post(self, request, pk):
        conversation = get_object_or_404(Conversation, pk=pk)
        form = AgentReplyForm(request.POST)
        if form.is_valid():
            services.agent_reply(
                conversation=conversation,
                user=request.user,
                text=form.cleaned_data["content"],
                request=request,
            )
        else:
            django_messages.error(request, "The reply cannot be empty.")
        return redirect("conversations:detail", pk=pk)


class ConversationTakeOverView(PageMixin, View):
    def post(self, request, pk):
        conversation = get_object_or_404(Conversation, pk=pk)
        form = HandoffForm(request.POST)
        reason = form.data.get("reason", "")
        services.take_over(
            conversation=conversation, user=request.user, reason=reason, request=request
        )
        django_messages.success(request, "You are now handling this chat.")
        return redirect("conversations:detail", pk=pk)


class ConversationResumeAIView(PageMixin, View):
    def post(self, request, pk):
        conversation = get_object_or_404(Conversation, pk=pk)
        services.resume_ai(conversation=conversation, user=request.user, request=request)
        django_messages.success(request, "The AI assistant has resumed this chat.")
        return redirect("conversations:detail", pk=pk)


class ConversationCloseView(PageMixin, View):
    def post(self, request, pk):
        conversation = get_object_or_404(Conversation, pk=pk)
        services.close_conversation(
            conversation=conversation, user=request.user, request=request
        )
        django_messages.success(request, "Conversation closed.")
        return redirect("conversations:detail", pk=pk)


class WidgetPreviewView(PageMixin, TemplateView):
    template_name = "conversations/widget_preview.html"
    page_title = "Chat Widget Preview"
    page_subtitle = "Try the embeddable widget exactly as a customer would see it."
    active_nav = "conversations"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from websites.models import WebsiteAPIKey
        from websites.selectors import list_websites

        websites = list_websites(status="active")
        website_id = self.request.GET.get("website")
        website = websites.filter(pk=website_id).first() or websites.first()
        api_key = (
            WebsiteAPIKey.objects.filter(website=website, is_active=True).first()
            if website
            else None
        )
        ctx["websites"] = websites
        ctx["website"] = website
        ctx["api_key"] = api_key
        return ctx


class AISettingsView(AdminRequiredMixin, FormView):
    template_name = "conversations/ai_settings.html"
    form_class = AISettingsForm
    page_title = "AI Settings"
    page_subtitle = "Choose which AI model answers website chats, and its API key."
    active_nav = "ai_settings"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = AISettings.load()
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        stored = AISettings.load()
        ctx["settings_row"] = stored
        ctx["active"] = ai.resolve_config(stored)
        ctx["key_source"] = (
            "saved here" if ctx["active"] and stored.stored_key(stored.provider) else "server environment"
        )
        return ctx

    def form_valid(self, form):
        changed_keys = form.save(user=self.request.user)
        stored = form.instance
        log_audit(
            actor=self.request.user,
            action="ai_settings.update",
            entity=stored,
            # Never record the keys themselves — only which ones changed.
            metadata={
                "enabled": stored.enabled,
                "provider": stored.provider,
                "model": stored.model,
                "keys_changed": changed_keys,
            },
            request=self.request,
        )
        if stored.enabled and ai.resolve_config(stored) is None:
            django_messages.warning(
                self.request,
                f"Saved, but there is no {stored.get_provider_display()} API key — "
                "chats will use rule-based replies until you add one.",
            )
        else:
            django_messages.success(self.request, "AI settings saved.")
        return redirect("conversations:ai_settings")


class AISettingsTestView(AdminRequiredMixin, View):
    """Checks the saved key and model against the provider. Generates nothing."""

    def post(self, request):
        config = ai.resolve_config(AISettings.load(), respect_enabled=False)
        if config is None:
            django_messages.error(request, "Save an API key for the selected provider first.")
        else:
            ok, message = ai.check_connection(config)
            (django_messages.success if ok else django_messages.error)(request, message)
        return redirect("conversations:ai_settings")
