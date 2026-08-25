from django.contrib import messages as django_messages
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import DetailView, TemplateView

from core.mixins import PageMixin
from core.selectors import paginate

from . import selectors, services
from .forms import AgentReplyForm, HandoffForm, InboxFilterForm
from .models import Conversation, ConversationStatus


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
