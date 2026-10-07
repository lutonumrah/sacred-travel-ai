from django.db.models import Max
from django.urls import path
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from bookings.services import BookingError
from bookings.services import payment_url as booking_payment_url
from core.api import EnvelopeMixin, ErrorResponse, SuccessResponse
from core.attribution import apply_attribution
from core.cors import allow_cors_for
from core.permissions import IsSalesTeam
from core.throttling import WidgetBookThrottle, WidgetChatThrottle, WidgetPollThrottle
from websites.selectors import active_key_for
from websites.services import touch_api_key

from . import selectors, services
from .forms import WidgetBookForm, WidgetChatForm
from .models import Conversation, ConversationStatus, MessageSender, Recommendation
from .serializers import (
    ConversationDetailSerializer,
    ConversationSerializer,
)

app_name = "api_conversations"


class InboxAPI(EnvelopeMixin, generics.ListAPIView):
    permission_classes = [IsSalesTeam]
    serializer_class = ConversationSerializer

    def get_queryset(self):
        return selectors.list_conversations(
            status=self.request.query_params.get("status", ""),
            q=self.request.query_params.get("q", ""),
            user=self.request.user,
        ).exclude(status=ConversationStatus.CLOSED)


class ConversationDetailAPI(EnvelopeMixin, generics.RetrieveAPIView):
    permission_classes = [IsSalesTeam]
    serializer_class = ConversationDetailSerializer

    def get_queryset(self):
        return selectors.visible_conversations(self.request.user).prefetch_related(
            "messages", "recommendations", "handoffs"
        )


class HandoffAPI(APIView):
    """Take over a chat, or hand it back to the AI."""

    permission_classes = [IsSalesTeam]

    def post(self, request, pk):
        conversation = generics.get_object_or_404(
            selectors.visible_conversations(request.user), pk=pk
        )
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


# Shown (not stored) when the customer writes while the chat waits for a person.
WAITING_NOTICE = "A travel consultant has been asked to join and will reply here shortly."


def _widget_status(conversation):
    return {
        "status": conversation.status,
        "awaiting_human": conversation.status in services.HUMAN_STATUSES,
    }


def _resolve_widget_key(request, key):
    """`(api_key, error_response)` for a public widget key."""
    api_key = active_key_for(key or "")
    if api_key is None:
        return None, ErrorResponse("Unknown or inactive widget key.", status_code=403)
    if not api_key.website.widget_enabled:
        return None, ErrorResponse(
            "The chat widget is disabled for this website.", status_code=403
        )
    allow_cors_for(request, api_key.website)
    touch_api_key(api_key)
    return api_key, None


def _widget_conversation(request, key, session):
    """`(conversation, error_response)` for a key + session pair from the widget."""
    api_key, error = _resolve_widget_key(request, key)
    if error is not None:
        return None, error
    conversation = (
        Conversation.objects.filter(session_key=session, website=api_key.website).first()
        if session
        else None
    )
    if conversation is None:
        return None, ErrorResponse("Unknown session.", status_code=404)
    return conversation, None


class WidgetChatAPI(APIView):
    """Public endpoint the embeddable chat widget posts to.

    Authenticated by the website's public widget key rather than a session, so
    it is deliberately open to anonymous callers.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get_throttles(self):
        # GET is the old polling call kept for widgets cached before /history/.
        if self.request.method == "GET":
            return [WidgetPollThrottle()]
        return [WidgetChatThrottle()]

    def post(self, request):
        form = WidgetChatForm(request.data)
        if not form.is_valid():
            return ErrorResponse("Invalid request.", detail=form.errors)

        data = form.cleaned_data
        api_key, error = _resolve_widget_key(request, data["key"])
        if error is not None:
            return error
        website = api_key.website

        conversation, created = services.start_conversation(
            website=website,
            session_key=data.get("session") or None,
            context={"source": website.source_identifier},
        )
        if conversation.website_id != website.pk:
            # A session key from another website must not be reused.
            return ErrorResponse("Session does not belong to this website.", status_code=403)

        if created:
            # UTM tags, referrer and landing page the widget read on the host
            # page; the lead and any booking inherit them.
            changed = apply_attribution(conversation, request.data.get("attribution"))
            if changed:
                conversation.save(update_fields=changed + ["updated_at"])

        # Any contact details the host page collected up front.
        provided = {
            key: data[key] for key in ("name", "email", "phone") if data.get(key)
        }
        if provided:
            context = dict(conversation.context or {})
            context.update(provided)
            conversation.context = context
            conversation.save(update_fields=["context", "updated_at"])

        before = conversation.messages.aggregate(last=Max("pk"))["last"] or 0
        turn = services.handle_customer_message(
            conversation=conversation, text=data["message"], request=request
        )

        conversation.refresh_from_db()
        # Only what this turn added — never the whole chat history. While a human
        # is involved there is no reply; never re-send an older message.
        new_messages = [
            row
            for row in selectors.widget_messages(conversation, since=before, request=request)
            if row["sender_type"] != MessageSender.CUSTOMER
        ]
        reply = None
        if turn.message is not None:
            reply = next((row for row in new_messages if row["id"] == turn.message.pk), None)
        notice = ""
        if reply is None and conversation.status == ConversationStatus.WAITING:
            notice = WAITING_NOTICE
        return SuccessResponse(
            {
                "session": conversation.session_key,
                "is_new_session": created,
                **_widget_status(conversation),
                "reply": reply,
                # Everything new for the visitor, `reply` included: e.g. the note
                # posted when the AI takes back an unanswered handoff.
                "messages": new_messages,
                "notice": notice,
                "recommendations": [
                    selectors.widget_card(rec) for rec in turn.recommendations[:4]
                ],
                "known": selectors.widget_known_details(conversation),
            }
        )

    def get(self, request):
        return _history_response(request)


def _history_response(request):
    conversation, error = _widget_conversation(
        request, request.query_params.get("key", ""), request.query_params.get("session", "")
    )
    if error is not None:
        return error
    services.maybe_auto_resume(conversation=conversation, request=request)
    since = request.query_params.get("since", "")
    return SuccessResponse(
        {
            "session": conversation.session_key,
            **_widget_status(conversation),
            "messages": selectors.widget_messages(
                conversation, since=int(since) if since.isdigit() else None, request=request
            ),
            "known": selectors.widget_known_details(conversation),
            "bookings": selectors.widget_bookings(conversation, request),
        }
    )


class WidgetHistoryAPI(APIView):
    """The chat so far for a session, so a reloaded page can restore it.

    With `since=<message id>` it returns only newer messages, which is how the
    widget polls for agent replies.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [WidgetPollThrottle]

    def get(self, request):
        return _history_response(request)


class WidgetBookAPI(APIView):
    """"Book this" on a recommendation card: raise a pending booking, return its pay link."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [WidgetBookThrottle]

    def post(self, request):
        form = WidgetBookForm(request.data)
        if not form.is_valid():
            return ErrorResponse("Please check the details.", detail=form.errors)
        data = form.cleaned_data
        conversation, error = _widget_conversation(request, data["key"], data["session"])
        if error is not None:
            return error
        recommendation = Recommendation.objects.filter(
            pk=data["recommendation_id"], conversation=conversation
        ).first()
        if recommendation is None:
            return ErrorResponse("That option is not part of this chat.", status_code=404)

        known = selectors.widget_known_details(conversation)
        missing = {
            field_name: ["This field is required."]
            for field_name in ("name", "email", "phone")
            if not (data.get(field_name) or known.get(field_name))
        }
        if missing:
            return ErrorResponse("Please check the details.", detail=missing)

        try:
            booking, created = services.book_from_chat(
                conversation=conversation,
                recommendation=recommendation,
                name=data.get("name") or known["name"],
                email=data.get("email") or known["email"],
                phone=data.get("phone") or known["phone"],
                travel_start=data["travel_start"],
                travel_end=data.get("travel_end"),
                travelers=data["travelers"],
                request=request,
            )
        except BookingError as exc:
            return ErrorResponse(str(exc))

        conversation.refresh_from_db()
        return SuccessResponse(
            {
                "created": created,
                **_widget_status(conversation),
                "booking": {
                    "number": booking.booking_number,
                    "status": booking.status,
                    "product": booking.product_name,
                    "room": booking.room_label,
                    "travel_start": booking.travel_start,
                    "travel_end": booking.travel_end,
                    "travelers": booking.travelers_count,
                    "currency": booking.currency,
                    "subtotal": booking.subtotal,
                    "tax": booking.tax_amount,
                    "total": booking.total_amount,
                    "pricing": (booking.summary or {}).get("pricing", {}).get("description", ""),
                },
                "payment_url": booking_payment_url(booking, request),
            },
            message="Booking created." if created else "You already have this booking.",
            status_code=201 if created else 200,
        )


urlpatterns = [
    path("inbox/", InboxAPI.as_view(), name="inbox"),
    path("widget/chat/", WidgetChatAPI.as_view(), name="widget_chat"),
    path("widget/history/", WidgetHistoryAPI.as_view(), name="widget_history"),
    path("widget/book/", WidgetBookAPI.as_view(), name="widget_book"),
    path("<int:pk>/", ConversationDetailAPI.as_view(), name="detail"),
    path("<int:pk>/handoff/", HandoffAPI.as_view(), name="handoff"),
]
