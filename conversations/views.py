from urllib.parse import urlencode

from django.contrib import messages as django_messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, TemplateView, UpdateView

from core.attribution import attribution_rows
from core.mixins import AdminRequiredMixin, ManagerRequiredMixin, SalesRequiredMixin
from core.selectors import paginate
from core.services import log_audit
from crm.selectors import preference_rows

from . import ai, selectors, services
from .forms import (
    AgentReplyForm,
    AISettingsForm,
    ConversationAssignForm,
    HandoffForm,
    InboxFilterForm,
    KnowledgeArticleForm,
    KnowledgeFilterForm,
    StaffBookForm,
)
from .models import (
    AISettings,
    Conversation,
    ConversationStatus,
    KnowledgeArticle,
    Recommendation,
)


def _inbox_context(request):
    """The open-chats table for the inbox page and its live refresh, same filters."""
    form = InboxFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_bound and form.is_valid() else {}
    queryset = selectors.list_conversations(
        q=data.get("q", "") or "",
        status=data.get("status", "") or "",
        website=data.get("website"),
        user=request.user,
    ).exclude(status=ConversationStatus.CLOSED)
    ctx = {
        "filter_form": form,
        "page_obj": paginate(queryset, request.GET.get("page")),
        "waiting": selectors.waiting_count(request.user),
        "total": queryset.count(),
        "querystring": urlencode(
            [(key, value) for key, value in request.GET.items() if key != "page" and value]
        ),
    }
    if request.user.is_manager:
        ctx["assignees"] = ConversationAssignForm().fields["assigned_to"].queryset
    return ctx


class InboxView(SalesRequiredMixin, TemplateView):
    template_name = "conversations/inbox.html"
    page_title = "Live Conversation Inbox"
    page_subtitle = "AI and human chats currently in progress. Updates every few seconds."
    active_nav = "conversations"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_inbox_context(self.request))
        return ctx


class InboxLiveView(SalesRequiredMixin, View):
    """The inbox table re-rendered for the page's poll: server-escaped HTML in JSON."""

    def get(self, request):
        ctx = _inbox_context(request)
        return JsonResponse(
            {
                "html": render_to_string("conversations/_inbox_live.html", ctx, request=request),
                "waiting": ctx["waiting"],
                "total": ctx["total"],
            }
        )


class ConversationHistoryView(SalesRequiredMixin, TemplateView):
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


class ConversationDetailView(SalesRequiredMixin, DetailView):
    model = Conversation
    template_name = "conversations/detail.html"
    context_object_name = "conversation"
    active_nav = "conversations"

    def get_queryset(self):
        return selectors.visible_conversations(self.request.user).select_related(
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
        messages_list = list(selectors.conversation_messages(conversation))
        ctx["messages_list"] = messages_list
        ctx["last_message_id"] = messages_list[-1].pk if messages_list else 0
        ctx["recommendations"] = conversation.recommendations.all()[:20]
        ctx["handoffs"] = conversation.handoffs.select_related("taken_by")
        ctx["reply_form"] = AgentReplyForm()
        ctx["handoff_form"] = HandoffForm()
        requirements = conversation.requirements or {}
        ctx["requirements"] = {
            key: value for key, value in requirements.items() if key != "preferences"
        }
        ctx["preference_rows"] = preference_rows(requirements.get("preferences"))
        ctx["contact"] = conversation.context or {}
        ctx["attribution_rows"] = attribution_rows(conversation)
        ctx["can_reply"] = conversation.status != ConversationStatus.CLOSED
        if self.request.user.is_manager:
            ctx["assign_form"] = ConversationAssignForm(
                initial={"assigned_to": conversation.assigned_to_id}
            )
        ctx["book_initial"] = {
            "travel_start": requirements.get("travel_start", ""),
            "travel_end": requirements.get("travel_end", ""),
            "travelers": requirements.get("travelers") or 1,
        }
        from bookings.selectors import visible_bookings

        ctx["bookings"] = visible_bookings(self.request.user).filter(conversation=conversation)
        return ctx


class ConversationLiveView(SalesRequiredMixin, View):
    """New messages since `?since=<id>` plus the chat's current status, for the detail page."""

    def get(self, request, pk):
        conversation = get_object_or_404(
            selectors.visible_conversations(request.user).select_related("assigned_to"), pk=pk
        )
        since = request.GET.get("since", "")
        messages_list = list(
            selectors.conversation_messages(conversation).filter(
                pk__gt=int(since) if since.isdigit() else 0
            )
        )
        return JsonResponse(
            {
                "status": conversation.status,
                "status_display": conversation.get_status_display(),
                "assigned_to": (
                    conversation.assigned_to.get_username() if conversation.assigned_to else ""
                ),
                "can_reply": conversation.status != ConversationStatus.CLOSED,
                "last_id": messages_list[-1].pk if messages_list else 0,
                "html": render_to_string(
                    "conversations/_messages.html", {"messages_list": messages_list}
                ),
                "actions_html": render_to_string(
                    "conversations/_detail_actions.html",
                    {"conversation": conversation},
                    request=request,
                ),
                "waiting": selectors.waiting_count(request.user),
            }
        )


class ConversationReplyView(SalesRequiredMixin, View):
    def post(self, request, pk):
        conversation = get_object_or_404(selectors.visible_conversations(request.user), pk=pk)
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


class ConversationTakeOverView(SalesRequiredMixin, View):
    def post(self, request, pk):
        conversation = get_object_or_404(selectors.visible_conversations(request.user), pk=pk)
        form = HandoffForm(request.POST)
        reason = form.data.get("reason", "")
        services.take_over(
            conversation=conversation, user=request.user, reason=reason, request=request
        )
        django_messages.success(request, "You are now handling this chat.")
        return redirect("conversations:detail", pk=pk)


class ConversationAssignView(ManagerRequiredMixin, View):
    """Managers hand a chat to an agent, or back to the unassigned pool."""

    def post(self, request, pk):
        conversation = get_object_or_404(selectors.visible_conversations(request.user), pk=pk)
        form = ConversationAssignForm(request.POST)
        if not form.is_valid():
            django_messages.error(request, "Pick an active team member.")
        else:
            user = form.cleaned_data["assigned_to"]
            services.assign_conversation(
                conversation=conversation, user=user, actor=request.user, request=request
            )
            django_messages.success(
                request,
                f"Chat assigned to {user.get_username()}." if user else "Chat unassigned.",
            )
        next_url = request.POST.get("next", "")
        if next_url == "inbox":
            return redirect("conversations:inbox")
        return redirect("conversations:detail", pk=pk)


class ConversationBookView(SalesRequiredMixin, View):
    """"Book this for the customer": the same service the widget uses."""

    def post(self, request, pk):
        from bookings.selectors import booking_contact
        from bookings.services import BookingError, booking_from_recommendation

        conversation = get_object_or_404(selectors.visible_conversations(request.user), pk=pk)
        form = StaffBookForm(request.POST)
        if not form.is_valid():
            django_messages.error(request, "Enter the travel date and number of travellers.")
            return redirect("conversations:detail", pk=pk)
        data = form.cleaned_data
        recommendation = get_object_or_404(
            Recommendation, pk=data["recommendation_id"], conversation=conversation
        )
        if conversation.customer is None:
            django_messages.error(
                request, "Capture the customer's email or phone in the chat before booking."
            )
            return redirect("conversations:detail", pk=pk)
        try:
            booking, created = booking_from_recommendation(
                recommendation=recommendation,
                customer=conversation.customer,
                travel_start=data["travel_start"],
                travel_end=data.get("travel_end"),
                travelers=data["travelers"],
                actor=request.user,
                request=request,
            )
        except BookingError as exc:
            django_messages.error(request, str(exc))
            return redirect("conversations:detail", pk=pk)
        django_messages.success(
            request,
            f"Booking {booking.booking_number} created"
            + (
                " — the payment link has been emailed to the customer."
                if booking_contact(booking)["email"]
                else " — send the customer the payment link."
            )
            if created
            else f"Booking {booking.booking_number} already exists for this option.",
        )
        return redirect("bookings:detail", pk=booking.pk)


class ConversationResumeAIView(SalesRequiredMixin, View):
    def post(self, request, pk):
        conversation = get_object_or_404(selectors.visible_conversations(request.user), pk=pk)
        services.resume_ai(conversation=conversation, user=request.user, request=request)
        django_messages.success(request, "The AI assistant has resumed this chat.")
        return redirect("conversations:detail", pk=pk)


class ConversationCloseView(SalesRequiredMixin, View):
    def post(self, request, pk):
        conversation = get_object_or_404(selectors.visible_conversations(request.user), pk=pk)
        services.close_conversation(
            conversation=conversation, user=request.user, request=request
        )
        django_messages.success(request, "Conversation closed.")
        return redirect("conversations:detail", pk=pk)


class WidgetPreviewView(SalesRequiredMixin, TemplateView):
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
                "handoff_wait_minutes": stored.handoff_wait_minutes,
                "agent_idle_minutes": stored.agent_idle_minutes,
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


class KnowledgeListView(ManagerRequiredMixin, TemplateView):
    template_name = "conversations/knowledge_list.html"
    page_title = "Knowledge Base"
    page_subtitle = "Policies, FAQs and travel information the AI may quote — and nothing else."
    active_nav = "knowledge"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = KnowledgeFilterForm(self.request.GET or None)
        data = form.cleaned_data if form.is_bound and form.is_valid() else {}
        queryset = selectors.list_knowledge(
            q=data.get("q", "") or "",
            category=data.get("category", "") or "",
            website=data.get("website"),
        )
        ctx["filter_form"] = form
        ctx["page_obj"] = paginate(queryset, self.request.GET.get("page"))
        ctx["total"] = queryset.count()
        ctx["ai_active"] = ai.resolve_config() is not None
        return ctx


class _KnowledgeSaveMixin:
    model = KnowledgeArticle
    form_class = KnowledgeArticleForm
    template_name = "conversations/knowledge_form.html"
    active_nav = "knowledge"

    def form_valid(self, form):
        self.object = services.save_knowledge_article(
            article=form.save(commit=False), actor=self.request.user, request=self.request
        )
        django_messages.success(self.request, f"“{self.object.title}” saved.")
        return redirect(reverse("conversations:knowledge"))


class KnowledgeCreateView(ManagerRequiredMixin, _KnowledgeSaveMixin, CreateView):
    page_title = "Add Knowledge Article"
    page_subtitle = "A policy, FAQ answer or travel fact the AI may use in chats."


class KnowledgeUpdateView(ManagerRequiredMixin, _KnowledgeSaveMixin, UpdateView):
    page_title = "Edit Knowledge Article"
    page_subtitle = "Changes apply to the very next chat message."


class KnowledgeDeleteView(ManagerRequiredMixin, View):
    def post(self, request, pk):
        article = get_object_or_404(KnowledgeArticle, pk=pk)
        services.delete_knowledge_article(article=article, actor=request.user, request=request)
        django_messages.success(request, f"“{article.title}” deleted.")
        return redirect("conversations:knowledge")
