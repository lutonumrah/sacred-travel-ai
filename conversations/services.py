"""Conversation orchestration: chat turns, lead capture and human handoff."""

import secrets
from dataclasses import dataclass, field

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.notifications import notify, notify_managers
from core.services import log_audit

from . import ai
from .models import (
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


@transaction.atomic
def handle_customer_message(*, conversation, text, request=None):
    """One full chat turn: store the question, think, answer, capture the lead."""
    post_message(
        conversation=conversation, sender_type=MessageSender.CUSTOMER, content=text
    )

    if conversation.status == ConversationStatus.HUMAN_ACTIVE:
        # A human owns this chat — record the message and let them answer.
        if conversation.assigned_to:
            notify(
                recipient=conversation.assigned_to,
                notification_type="handoff",
                title="New customer message",
                body=text[:140],
                link=reverse("conversations:detail", args=[conversation.pk]),
                metadata={"conversation_id": conversation.pk},
            )
        return None

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
        },
    )

    sync_lead(conversation=conversation, request=request)

    if result.should_handoff:
        request_handoff(
            conversation=conversation, reason=result.handoff_reason, request=request
        )

    return TurnResult(message=ai_message, recommendations=saved_recommendations)


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
        customer, _created = crm_services.get_or_create_customer(
            first_name=context.get("name") or "Website visitor",
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
            preferences=requirements,
        )
        _apply_requirements(lead, requirements)
        crm_services.create_lead(
            lead=lead,
            request=request,
            activity_summary=f"Lead captured from AI chat {conversation.session_key[:8]}",
        )
        conversation.lead = lead
        conversation.save(update_fields=["lead", "updated_at"])
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
    if requirements != lead.preferences:
        lead.preferences = requirements
        changed.append("preferences")
    if _apply_requirements(lead, requirements):
        changed += ["travel_start", "travel_end", "budget_max", "travelers_count"]
    if changed:
        lead.score = crm_services.score_lead(lead)
        lead.save(update_fields=list(set(changed)) + ["score", "updated_at"])
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
    budget = requirements.get("budget_max")
    if budget and lead.budget_max != budget:
        lead.budget_max = budget
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

    conversation.status = ConversationStatus.WAITING
    conversation.save(update_fields=["status", "updated_at"])
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
    conversation.save(update_fields=["status", "updated_at"])
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
