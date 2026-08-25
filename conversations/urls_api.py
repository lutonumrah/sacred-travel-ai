from django.urls import path
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from core.api import EnvelopeMixin, SuccessResponse
from websites.selectors import active_key_for
from websites.services import touch_api_key

from . import selectors, services
from .forms import WidgetChatForm
from .models import Conversation, ConversationStatus, MessageSender
from .serializers import (
    ConversationDetailSerializer,
    ConversationSerializer,
    MessageSerializer,
)

app_name = "api_conversations"


class InboxAPI(EnvelopeMixin, generics.ListAPIView):
    serializer_class = ConversationSerializer

    def get_queryset(self):
        return selectors.list_conversations(
            status=self.request.query_params.get("status", ""),
            q=self.request.query_params.get("q", ""),
            user=self.request.user,
        ).exclude(status=ConversationStatus.CLOSED)


class ConversationDetailAPI(EnvelopeMixin, generics.RetrieveAPIView):
    serializer_class = ConversationDetailSerializer

    def get_queryset(self):
        return Conversation.objects.prefetch_related(
            "messages", "recommendations", "handoffs"
        )


class HandoffAPI(APIView):
    """Take over a chat, or hand it back to the AI."""

    def post(self, request, pk):
        conversation = generics.get_object_or_404(Conversation, pk=pk)
        action = request.data.get("action", "take_over")
        if action == "resume_ai":
            services.resume_ai(conversation=conversation, user=request.user, request=request)
        elif action == "close":
            services.close_conversation(
                conversation=conversation, user=request.user, request=request
            )
        else:
            services.take_over(
                conversation=conversation,
                user=request.user,
                reason=request.data.get("reason", ""),
                request=request,
            )
        conversation.refresh_from_db()
        return SuccessResponse(
            ConversationSerializer(conversation).data, message=f"Conversation {action}."
        )


class WidgetChatAPI(APIView):
    """Public endpoint the embeddable chat widget posts to.

    Authenticated by the website's public widget key rather than a session, so
    it is deliberately open to anonymous callers.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        form = WidgetChatForm(request.data)
        if not form.is_valid():
            return SuccessResponse(
                {"errors": form.errors}, message="Invalid request.", status_code=400
            )

        data = form.cleaned_data
        api_key = active_key_for(data["key"])
        if api_key is None:
            return SuccessResponse(
                None, message="Unknown or inactive widget key.", status_code=403
            )
        website = api_key.website
        if not website.widget_enabled:
            return SuccessResponse(
                None, message="The chat widget is disabled for this website.", status_code=403
            )
        touch_api_key(api_key)

        conversation, created = services.start_conversation(
            website=website,
            session_key=data.get("session") or None,
            context={"source": website.source_identifier},
        )
        if conversation.website_id != website.pk:
            # A session key from another website must not be reused.
            return SuccessResponse(
                None, message="Session does not belong to this website.", status_code=403
            )

        # Any contact details the host page collected up front.
        provided = {
            key: data[key] for key in ("name", "email", "phone") if data.get(key)
        }
        if provided:
            context = dict(conversation.context or {})
            context.update(provided)
            conversation.context = context
            conversation.save(update_fields=["context", "updated_at"])

        turn = services.handle_customer_message(
            conversation=conversation, text=data["message"], request=request
        )

        conversation.refresh_from_db()
        # Only this turn's answer and cards — never the whole chat history.
        reply = turn.message if turn else None
        if reply is None:
            reply = (
                conversation.messages.filter(is_internal=False)
                .exclude(sender_type=MessageSender.CUSTOMER)
                .last()
            )
        return SuccessResponse(
            {
                "session": conversation.session_key,
                "status": conversation.status,
                "is_new_session": created,
                "awaiting_human": conversation.status
                in (ConversationStatus.WAITING, ConversationStatus.HUMAN_ACTIVE),
                "reply": MessageSerializer(reply).data if reply else None,
                "recommendations": [
                    {
                        "type": rec.inventory_type,
                        "id": rec.object_id,
                        "title": rec.title,
                        "price": rec.price,
                        "currency": rec.currency,
                        "detail": (rec.payload or {}).get("detail", ""),
                    }
                    for rec in (turn.recommendations if turn else [])[:4]
                ],
            }
        )

    def get(self, request):
        """Poll for messages a human agent has sent since the widget last asked."""
        key = request.query_params.get("key", "")
        session = request.query_params.get("session", "")
        api_key = active_key_for(key)
        if api_key is None or not session:
            return SuccessResponse(None, message="Unknown widget key.", status_code=403)
        conversation = Conversation.objects.filter(
            session_key=session, website=api_key.website
        ).first()
        if conversation is None:
            return SuccessResponse(None, message="Unknown session.", status_code=404)
        messages = conversation.messages.filter(is_internal=False)
        since = request.query_params.get("since")
        if since:
            messages = messages.filter(pk__gt=since)
        return SuccessResponse(
            {
                "status": conversation.status,
                "messages": MessageSerializer(messages, many=True).data,
            }
        )


urlpatterns = [
    path("inbox/", InboxAPI.as_view(), name="inbox"),
    path("widget/chat/", WidgetChatAPI.as_view(), name="widget_chat"),
    path("<int:pk>/", ConversationDetailAPI.as_view(), name="detail"),
    path("<int:pk>/handoff/", HandoffAPI.as_view(), name="handoff"),
]
