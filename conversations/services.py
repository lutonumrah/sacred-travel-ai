"""Conversation orchestration: chat turns, lead capture and human handoff."""

import secrets
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from core.notifications import notify, notify_managers
from core.services import log_audit

from . import ai
from .models import (
    AISettings,
    Conversation,
    ConversationHandoff,
    ConversationStatus,
    Message,
    MessageSender,
    Recommendation,
)


@dataclass
class TurnResult:
    """What one chat turn produced, so callers do not re-read the whole history."""

    message: object = None
    recommendations: list = field(default_factory=list)


def new_session_key():
    return secrets.token_urlsafe(24)[:64]


def start_conversation(*, website=None, session_key=None, context=None):
    """Fetch the live conversation for a session key, or open a new one."""
    if session_key:
        existing = Conversation.objects.filter(session_key=session_key).first()
        if existing:
            return existing, False
    conversation = Conversation.objects.create(
        website=website,
        session_key=session_key or new_session_key(),
        context=context or {},
        status=ConversationStatus.AI_ACTIVE,
    )
    return conversation, True


def post_message(*, conversation, sender_type, content, sender_user=None, metadata=None, is_internal=False):
    message = Message.objects.create(
        conversation=conversation,
        sender_type=sender_type,
        sender_user=sender_user,
        content=content,
        metadata=metadata or {},
        is_internal=is_internal,
    )
    Conversation.objects.filter(pk=conversation.pk).update(
        last_message_at=message.created_at
    )
    conversation.last_message_at = message.created_at
    return message


def build_history(conversation, limit=None):
    """Recent turns in the shape the Claude Messages API expects."""
    from django.conf import settings

    limit = limit or getattr(settings, "AI_MAX_HISTORY", 20)
    messages = (
        conversation.messages.filter(is_internal=False)
        .exclude(sender_type=MessageSender.SYSTEM)
        .order_by("-created_at")[:limit]
    )
    history = []
    for message in reversed(list(messages)):
        role = "user" if message.sender_type == MessageSender.CUSTOMER else "assistant"
        # The API rejects two consecutive turns only at the boundaries; merging
        # same-role runs keeps the transcript clean either way.
        if history and history[-1]["role"] == role:
            history[-1]["content"] += f"\n{message.content}"
        else:
            history.append({"role": role, "content": message.content})
    while history and history[0]["role"] != "user":
        history.pop(0)
    return history


def save_recommendations(*, conversation, items):
    rows = [
        Recommendation(
            conversation=conversation,
            inventory_type=item["inventory_type"],
            object_id=item["id"],
            title=item["name"],
            price=item.get("price"),
            currency=item.get("currency", "INR"),
            payload={
                "detail": item.get("detail", ""),
                "destination": item.get("destination", ""),
                "price_label": item.get("price_label", ""),
            },
        )
        for item in items
    ]
    return Recommendation.objects.bulk_create(rows)


# A person is (or is about to be) answering: the AI stays quiet.
HUMAN_STATUSES = (ConversationStatus.WAITING, ConversationStatus.HUMAN_ACTIVE)


@transaction.atomic
def handle_customer_message(*, conversation, text, request=None):
    """One full chat turn: store the question, think, answer, capture the lead.

    While a human is involved (or has been asked for) the message is only
    queued for them; the returned TurnResult then carries no reply.
    """
    # A timed-out handoff hands this very message back to the AI.
    maybe_auto_resume(conversation=conversation, request=request)

    post_message(
        conversation=conversation, sender_type=MessageSender.CUSTOMER, content=text
    )

    if conversation.status in HUMAN_STATUSES:
        _absorb_details(conversation=conversation, text=text, request=request)
        if conversation.assigned_to:
            notify(
                recipient=conversation.assigned_to,
                notification_type="handoff",
                title="New customer message",
                body=text[:140],
                link=reverse("conversations:detail", args=[conversation.pk]),
                metadata={"conversation_id": conversation.pk},
                # One per message: in-app only, or a busy chat floods the inbox.
                email=False,
            )
        return TurnResult()

    history = build_history(conversation)
    # `build_history` already includes the message just stored.
    if history and history[-1]["role"] == "user":
        history = history[:-1]

    result = ai.generate_reply(conversation=conversation, message=text, history=history)

    conversation.requirements = result.requirements
    if result.contact:
        context = dict(conversation.context or {})
        context.update(result.contact)
        conversation.context = context
    conversation.save(update_fields=["requirements", "context", "updated_at"])

    saved_recommendations = []
    if result.recommendations:
        saved_recommendations = save_recommendations(
            conversation=conversation, items=result.recommendations
        )

    ai_message = post_message(
        conversation=conversation,
        sender_type=MessageSender.AI,
        content=result.reply,
        metadata={
            "engine": result.engine,
            "recommended": [
                f"{item['inventory_type']}:{item['id']}" for item in result.recommendations
            ],
            # Lets the widget redraw this turn's cards when it reloads history.
            "recommendation_ids": [rec.pk for rec in saved_recommendations if rec.pk],
        },
    )

    sync_lead(conversation=conversation, request=request)

    if result.should_handoff:
        request_handoff(
            conversation=conversation, reason=result.handoff_reason, request=request
        )

    return TurnResult(message=ai_message, recommendations=saved_recommendations)


def _absorb_details(*, conversation, text, request=None):
    """Rules-only extraction for messages the AI does not answer.

    A customer waiting for an agent often types their phone number or dates;
    the lead should still pick those up.
    """
    requirements = ai.extract_requirements(text, conversation.requirements or {})
    contact = ai.extract_contact(text)
    if requirements == (conversation.requirements or {}) and not contact:
        return
    conversation.requirements = requirements
    if contact:
        conversation.context = {**(conversation.context or {}), **contact}
    conversation.save(update_fields=["requirements", "context", "updated_at"])
    sync_lead(conversation=conversation, request=request)


def lead_preferences(requirements):
    """What goes into `Lead.preferences`: stated wishes plus trip shape, blanks dropped."""
    from crm.selectors import PREFERENCE_LABELS

    requirements = requirements or {}
    source = {**(requirements.get("preferences") or {})}
    for key in ("product_type", "nights"):
        if requirements.get(key):
            source[key] = requirements[key]
    keys = {key for key, _label in PREFERENCE_LABELS}
    return {
        key: value
        for key, value in source.items()
        if key in keys and value not in (None, "", 0, [], {})
    }


def _enough_for_a_lead(conversation):
    """A lead needs a way to reach the customer, or a concrete trip request."""
    context = conversation.context or {}
    requirements = conversation.requirements or {}
    has_contact = bool(context.get("email") or context.get("phone"))
    has_trip = bool(requirements.get("destination"))
    return has_contact or has_trip


@transaction.atomic
def sync_lead(*, conversation, request=None):
    """Create or refresh the CRM lead behind this conversation."""
    from crm import services as crm_services
    from crm.models import Lead, LeadSource

    if not _enough_for_a_lead(conversation):
        return None

    context = conversation.context or {}
    requirements = conversation.requirements or {}

    customer = conversation.customer
    if customer is None and (context.get("email") or context.get("phone")):
        first_name, _, last_name = (context.get("name") or "").strip().partition(" ")
        customer, _created = crm_services.get_or_create_customer(
            first_name=first_name[:100] or "Website visitor",
            last_name=last_name.strip()[:100],
            email=context.get("email", ""),
            phone=context.get("phone", ""),
        )
        conversation.customer = customer
        conversation.save(update_fields=["customer", "updated_at"])

    destination = requirements.get("destination") or ""
    title = f"{destination} enquiry" if destination else "Website chat enquiry"

    lead = conversation.lead
    if lead is None:
        lead = Lead(
            customer=customer,
            website=conversation.website,
            title=title,
            source=LeadSource.AI_CHAT,
            destination=destination,
            travelers_count=requirements.get("travelers") or 1,
            preferences=lead_preferences(requirements),
        )
        _apply_requirements(lead, requirements)
        crm_services.create_lead(
            lead=lead,
            request=request,
            activity_summary=f"Lead captured from AI chat {conversation.session_key[:8]}",
        )
        conversation.lead = lead
        conversation.save(update_fields=["lead", "updated_at"])
        crm_services.qualify_if_ready(lead=lead, requirements=requirements, request=request)
        return lead

    # Refresh an existing lead with anything new the chat revealed.
    changed = []
    if customer and lead.customer_id != customer.pk:
        lead.customer = customer
        changed.append("customer")
    if destination and lead.destination != destination:
        lead.destination = destination
        lead.title = title
        changed += ["destination", "title"]
    preferences = lead_preferences(requirements)
    if preferences != lead.preferences:
        lead.preferences = preferences
        changed.append("preferences")
    if _apply_requirements(lead, requirements):
        changed += [
            "travel_start", "travel_end", "budget_min", "budget_max", "travelers_count"
        ]
    if changed:
        lead.score = crm_services.score_lead(lead)
        lead.save(update_fields=list(set(changed)) + ["score", "updated_at"])
    crm_services.qualify_if_ready(lead=lead, requirements=requirements, request=request)
    return lead


def _apply_requirements(lead, requirements):
    """Copy chat-extracted trip details onto the lead. Returns True if changed."""
    from datetime import date

    changed = False

    def _as_date(value):
        try:
            return date.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    start = _as_date(requirements.get("travel_start"))
    end = _as_date(requirements.get("travel_end"))
    if start and lead.travel_start != start:
        lead.travel_start = start
        changed = True
    if end and lead.travel_end != end:
        lead.travel_end = end
        changed = True
    for field_name in ("budget_min", "budget_max"):
        budget = requirements.get(field_name)
        if budget and getattr(lead, field_name) != budget:
            setattr(lead, field_name, budget)
            changed = True
    travelers = requirements.get("travelers")
    if travelers and lead.travelers_count != travelers:
        lead.travelers_count = travelers
        changed = True
    return changed


def request_handoff(*, conversation, reason="", actor=None, request=None):
    """Flag a chat as needing a person and alert the team."""
    if conversation.status == ConversationStatus.HUMAN_ACTIVE:
        return conversation

    if conversation.status != ConversationStatus.WAITING:
        conversation.handoff_requested_at = timezone.now()
    conversation.status = ConversationStatus.WAITING
    conversation.save(update_fields=["status", "handoff_requested_at", "updated_at"])
    post_message(
        conversation=conversation,
        sender_type=MessageSender.SYSTEM,
        content=f"Handoff requested — {reason or 'customer needs a human agent'}.",
        metadata={"reason": reason},
    )
    link = reverse("conversations:detail", args=[conversation.pk])
    if conversation.assigned_to:
        notify(
            recipient=conversation.assigned_to,
            notification_type="handoff",
            title="Chat needs you",
            body=reason,
            link=link,
            metadata={"conversation_id": conversation.pk},
        )
    else:
        notify_managers(
            notification_type="handoff",
            title="Chat waiting for a human",
            body=reason or "The AI asked for a takeover.",
            link=link,
            metadata={"conversation_id": conversation.pk},
        )
    log_audit(
        actor=actor,
        action="conversation.handoff_requested",
        entity=conversation,
        metadata={"reason": reason},
        request=request,
    )
    return conversation


def take_over(*, conversation, user, reason="", request=None):
    """A human agent claims the chat; the AI stops replying."""
    conversation.status = ConversationStatus.HUMAN_ACTIVE
    conversation.assigned_to = user
    conversation.save(update_fields=["status", "assigned_to", "updated_at"])
    handoff = ConversationHandoff.objects.create(
        conversation=conversation, taken_by=user, reason=reason
    )
    post_message(
        conversation=conversation,
        sender_type=MessageSender.SYSTEM,
        content=f"{user.get_username()} joined the chat.",
    )
    log_audit(
        actor=user,
        action="conversation.take_over",
        entity=conversation,
        metadata={"reason": reason},
        request=request,
    )
    return handoff


def resume_ai(*, conversation, user=None, request=None):
    """Hand the chat back to the AI."""
    conversation.status = ConversationStatus.AI_ACTIVE
    conversation.handoff_requested_at = None
    conversation.save(update_fields=["status", "handoff_requested_at", "updated_at"])
    handoff = conversation.handoffs.filter(resumed_ai_at__isnull=True).first()
    if handoff:
        handoff.resumed_ai_at = timezone.now()
        handoff.save(update_fields=["resumed_ai_at", "updated_at"])
    post_message(
        conversation=conversation,
        sender_type=MessageSender.SYSTEM,
        content="AI assistant resumed this conversation.",
    )
    log_audit(actor=user, action="conversation.resume_ai", entity=conversation, request=request)
    return conversation


def agent_reply(*, conversation, user, text, request=None):
    if conversation.status != ConversationStatus.HUMAN_ACTIVE:
        take_over(conversation=conversation, user=user, reason="Replied directly", request=request)
    return post_message(
        conversation=conversation,
        sender_type=MessageSender.AGENT,
        content=text,
        sender_user=user,
    )


def close_conversation(*, conversation, user=None, request=None):
    conversation.status = ConversationStatus.CLOSED
    conversation.closed_at = timezone.now()
    conversation.save(update_fields=["status", "closed_at", "updated_at"])
    post_message(
        conversation=conversation,
        sender_type=MessageSender.SYSTEM,
        content="Conversation closed.",
    )
    log_audit(actor=user, action="conversation.close", entity=conversation, request=request)
    return conversation


@transaction.atomic
def assign_conversation(*, conversation, user, actor, request=None):
    """A manager hands a chat to an agent (or back to the pool with user=None)."""
    previous = conversation.assigned_to
    if previous == user:
        return conversation
    conversation.assigned_to = user
    conversation.save(update_fields=["assigned_to", "updated_at"])
    target = user.get_username() if user else "nobody"
    post_message(
        conversation=conversation,
        sender_type=MessageSender.SYSTEM,
        content=f"Assigned to {target} by {actor.get_username()}.",
        sender_user=actor,
        is_internal=True,
    )
    log_audit(
        actor=actor,
        action="conversation.assign",
        entity=conversation,
        metadata={
            "from": previous.get_username() if previous else None,
            "to": user.get_username() if user else None,
        },
        request=request,
    )
    if user is not None and user != actor:
        notify(
            recipient=user,
            notification_type="handoff",
            title="Chat assigned to you",
            body=(
                "The customer is waiting for a human."
                if conversation.status == ConversationStatus.WAITING
                else f"Assigned by {actor.get_username()}."
            ),
            link=reverse("conversations:detail", args=[conversation.pk]),
            metadata={"conversation_id": conversation.pk},
        )
    return conversation


# --------------------------------------------------------------------------
# AI auto-resume
# --------------------------------------------------------------------------
#
# Checked lazily — whenever the customer sends a message or the widget polls —
# so no scheduler is needed. `auto_resume_stale_conversations` does the same
# sweep for a periodic job.

AUTO_RESUME_MESSAGES = {
    "handoff_wait": (
        "Sorry — all our travel consultants are busy right now. I'm back to help "
        "in the meantime, and a consultant will still follow up with you."
    ),
    "agent_idle": (
        "Sorry for the wait — your consultant has stepped away. I'll keep helping "
        "you here in the meantime."
    ),
}


def auto_resume_reason(conversation, *, now=None, config=None):
    """Why the AI should take this chat back now, or "" if it should not."""
    now = now or timezone.now()
    config = config or AISettings.load()

    if conversation.status == ConversationStatus.WAITING and config.handoff_wait_minutes:
        since = conversation.handoff_requested_at or conversation.updated_at
        if now - since >= timedelta(minutes=config.handoff_wait_minutes):
            return "handoff_wait"

    if conversation.status == ConversationStatus.HUMAN_ACTIVE and config.agent_idle_minutes:
        # The agent's last sign of life: their last reply, or taking the chat over.
        last_reply = conversation.messages.filter(
            sender_type=MessageSender.AGENT
        ).aggregate(at=Max("created_at"))["at"]
        took_over = conversation.handoffs.aggregate(at=Max("created_at"))["at"]
        active_at = max([moment for moment in (last_reply, took_over) if moment], default=None)
        waiting = conversation.messages.filter(sender_type=MessageSender.CUSTOMER)
        if active_at:
            waiting = waiting.filter(created_at__gt=active_at)
        oldest = waiting.order_by("created_at").values_list("created_at", flat=True).first()
        if oldest and now - oldest >= timedelta(minutes=config.agent_idle_minutes):
            return "agent_idle"
    return ""


@transaction.atomic
def maybe_auto_resume(*, conversation, now=None, config=None, request=None):
    """Give the chat back to the AI if a human never picked it up. Returns True if it did."""
    if conversation.status not in HUMAN_STATUSES:
        return False
    reason = auto_resume_reason(conversation, now=now, config=config)
    if not reason:
        return False
    # Conditional update: of two simultaneous polls, only one resumes the chat.
    claimed = Conversation.objects.filter(
        pk=conversation.pk, status=conversation.status
    ).update(
        status=ConversationStatus.AI_ACTIVE,
        handoff_requested_at=None,
        updated_at=timezone.now(),
    )
    if not claimed:
        conversation.refresh_from_db()
        return False
    conversation.status = ConversationStatus.AI_ACTIVE
    conversation.handoff_requested_at = None
    conversation.handoffs.filter(resumed_ai_at__isnull=True).update(resumed_ai_at=timezone.now())

    post_message(
        conversation=conversation,
        sender_type=MessageSender.AI,
        content=AUTO_RESUME_MESSAGES[reason],
        metadata={"engine": "auto_resume", "reason": reason},
    )
    log_audit(
        action="conversation.auto_resume",
        entity=conversation,
        metadata={"reason": reason},
        request=request,
    )
    title = (
        "AI took back an unanswered handoff"
        if reason == "handoff_wait"
        else "AI resumed a chat you left unanswered"
    )
    link = reverse("conversations:detail", args=[conversation.pk])
    if conversation.assigned_to:
        notify(
            recipient=conversation.assigned_to,
            notification_type="handoff",
            title=title,
            link=link,
            metadata={"conversation_id": conversation.pk, "reason": reason},
        )
    else:
        notify_managers(
            notification_type="handoff",
            title=title,
            link=link,
            metadata={"conversation_id": conversation.pk, "reason": reason},
        )
    return True


def auto_resume_stale_conversations(*, now=None):
    """Sweep every waiting / human chat. For a scheduler; returns how many resumed."""
    config = AISettings.load()
    if not (config.handoff_wait_minutes or config.agent_idle_minutes):
        return 0
    resumed = 0
    for conversation in Conversation.objects.filter(status__in=HUMAN_STATUSES):
        if maybe_auto_resume(conversation=conversation, now=now, config=config):
            resumed += 1
    return resumed


# --------------------------------------------------------------------------
# Booking from the chat
# --------------------------------------------------------------------------

PLACEHOLDER_NAMES = {"website visitor", "guest", ""}


@transaction.atomic
def book_from_chat(
    *,
    conversation,
    recommendation,
    name,
    email="",
    phone="",
    travel_start,
    travel_end=None,
    travelers=1,
    request=None,
):
    """The customer pressed "Book this" in the widget.

    Records what they told us on the conversation, makes sure a Customer and
    Lead exist, then raises the booking. Returns `(booking, created)`; raises
    `bookings.services.BookingError` with a customer-safe message.
    """
    from bookings import services as booking_services

    context = dict(conversation.context or {})
    for key, value in (("name", name), ("email", email), ("phone", phone)):
        if value:
            context[key] = value
    requirements = dict(conversation.requirements or {})
    requirements["travel_start"] = travel_start.isoformat()
    if travel_end:
        requirements["travel_end"] = travel_end.isoformat()
    requirements["travelers"] = travelers
    conversation.context = context
    conversation.requirements = requirements
    conversation.save(update_fields=["context", "requirements", "updated_at"])

    sync_lead(conversation=conversation, request=request)
    customer = conversation.customer
    if customer is None:
        raise booking_services.BookingError("We need an email address or phone number to book.")
    updated = []
    if customer.first_name.strip().lower() in PLACEHOLDER_NAMES and name:
        first, _, last = name.strip().partition(" ")
        customer.first_name, customer.last_name = first[:100], last.strip()[:100]
        updated += ["first_name", "last_name"]
    for field_name, value in (("email", email), ("phone", phone)):
        if value and not getattr(customer, field_name):
            setattr(customer, field_name, value)
            updated.append(field_name)
    if updated:
        customer.save(update_fields=updated + ["updated_at"])

    booking, created = booking_services.booking_from_recommendation(
        recommendation=recommendation,
        customer=customer,
        travel_start=travel_start,
        travel_end=travel_end,
        travelers=travelers,
        # Shown on the pay page, used for checkout prefill and the payment-link
        # email: the details typed in this chat, not whatever an existing
        # customer record holds.
        guest={"name": name, "email": email or customer.email, "phone": phone or customer.phone},
        request=request,
    )
    return booking, created
