from django.db.models import Count, Max, Q

from .models import (
    Conversation,
    ConversationStatus,
    KnowledgeArticle,
    KnowledgeCategory,
    MessageSender,
)


def visible_conversations(user):
    """Managers see every chat; employees their own and unassigned ones.

    Unassigned includes waiting chats, so any employee can open one to take it over.
    """
    queryset = Conversation.objects.all()
    if not (user.is_superuser or user.is_manager):
        queryset = queryset.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))
    return queryset


def list_conversations(*, status="", website=None, assigned_to=None, q="", user=None):
    base = visible_conversations(user) if user is not None else Conversation.objects.all()
    queryset = base.select_related(
        "website", "customer", "lead", "assigned_to"
    ).annotate(message_count=Count("messages"), latest=Max("messages__created_at")).order_by(
        "-last_message_at", "-created_at"
    )
    if status:
        queryset = queryset.filter(status=status)
    if website:
        queryset = queryset.filter(website=website)
    if assigned_to:
        queryset = queryset.filter(assigned_to=assigned_to)
    if q:
        queryset = queryset.filter(
            Q(session_key__icontains=q)
            | Q(customer__first_name__icontains=q)
            | Q(customer__last_name__icontains=q)
            | Q(customer__email__icontains=q)
            | Q(messages__content__icontains=q)
        ).distinct()
    return queryset


def live_conversations(*, user=None):
    """Everything still open, waiting chats first."""
    return list_conversations(user=user).exclude(status=ConversationStatus.CLOSED)


def waiting_count(user=None):
    """Chats waiting for a person; with `user`, only ones that user can open."""
    base = visible_conversations(user) if user is not None else Conversation.objects.all()
    return base.filter(status=ConversationStatus.WAITING).count()


def conversation_messages(conversation, *, include_internal=True):
    queryset = conversation.messages.select_related("sender_user")
    if not include_internal:
        queryset = queryset.filter(is_internal=False)
    return queryset


# --------------------------------------------------------------------------
# What the public widget may see
# --------------------------------------------------------------------------


def widget_known_details(conversation):
    """Prefill for the widget's booking form.

    Only what this chat itself was told — never the linked Customer record, which
    may be someone else's if a visitor typed their email address.
    """
    context = conversation.context or {}
    requirements = conversation.requirements or {}
    return {
        "name": context.get("name", ""),
        "email": context.get("email", ""),
        "phone": context.get("phone", ""),
        "travel_start": requirements.get("travel_start", ""),
        "travel_end": requirements.get("travel_end", ""),
        "travelers": requirements.get("travelers") or None,
    }


def widget_card(recommendation):
    payload = recommendation.payload or {}
    return {
        "recommendation_id": recommendation.pk,
        "type": recommendation.inventory_type,
        "id": recommendation.object_id,
        "title": recommendation.title,
        "price": recommendation.price,
        "currency": recommendation.currency,
        "price_label": payload.get("price_label", ""),
        "detail": payload.get("detail", ""),
        "offer": (payload.get("offer") or {}).get("title", ""),
        "bookable": bool(recommendation.price),
    }


def widget_messages(conversation, *, since=None, message_ids=None, request=None):
    """Customer-visible messages with the cards each AI turn showed.

    Internal notes and metadata (engine, staff usernames) stay out.
    """
    messages = conversation.messages.filter(is_internal=False).select_related("sender_user")
    if since:
        messages = messages.filter(pk__gt=since)
    if message_ids is not None:
        messages = messages.filter(pk__in=message_ids)
    messages = list(messages)
    wanted = {
        rec_id
        for message in messages
        for rec_id in (message.metadata or {}).get("recommendation_ids", [])
    }
    cards = {
        rec.pk: widget_card(rec)
        for rec in conversation.recommendations.filter(pk__in=wanted)
    }
    # Booking notes ("booking created / confirmed") carry the booking's pay link.
    booking_ids = {
        (message.metadata or {}).get("booking_id")
        for message in messages
        if (message.metadata or {}).get("event", "").startswith("booking_")
    } - {None}
    bookings = {}
    if booking_ids:
        rows = widget_bookings(
            conversation, request, bookings=conversation.bookings.filter(pk__in=booking_ids)
        )
        bookings = {row["id"]: row for row in rows}
    rows = []
    for message in messages:
        sender_name = ""
        if message.sender_type == MessageSender.AGENT:
            user = message.sender_user
            sender_name = (user.first_name if user else "") or "Travel consultant"
        metadata = message.metadata or {}
        rows.append(
            {
                "id": message.pk,
                "sender_type": message.sender_type,
                "sender_name": sender_name,
                "content": message.content,
                "created_at": message.created_at,
                "event": metadata.get("event", ""),
                "booking": bookings.get(metadata.get("booking_id")),
                "cards": [
                    cards[rec_id]
                    for rec_id in (message.metadata or {}).get("recommendation_ids", [])
                    if rec_id in cards
                ][:4],
            }
        )
    return rows


def widget_bookings(conversation, request=None, *, bookings=None):
    """This chat's bookings, with the pay link while it is still payable."""
    from bookings import services as booking_services

    if bookings is None:
        bookings = conversation.bookings.all()
    rows = []
    for booking in bookings.order_by("created_at"):
        payable = booking.is_payable and booking_services.has_live_payment_link(booking)
        rows.append(
            {
                "id": booking.pk,
                "number": booking.booking_number,
                "status": booking.status,
                "status_display": booking.get_status_display(),
                "product": booking.product_name,
                "room": booking.room_label,
                "total": booking.total_amount,
                "currency": booking.currency,
                "payment_url": booking_services.payment_url(booking, request) if payable else "",
            }
        )
    return rows


# --------------------------------------------------------------------------
# Knowledge base
# --------------------------------------------------------------------------

# Policies first: if the prompt budget runs out, travel tips go, not the refund rules.
KNOWLEDGE_PRIORITY = [
    KnowledgeCategory.POLICY,
    KnowledgeCategory.CANCELLATION,
    KnowledgeCategory.PAYMENT,
    KnowledgeCategory.FAQ,
    KnowledgeCategory.TRAVEL_INFO,
    KnowledgeCategory.OTHER,
]


def list_knowledge(*, q="", category="", website=None):
    queryset = KnowledgeArticle.objects.select_related("website", "updated_by")
    if q:
        queryset = queryset.filter(
            Q(title__icontains=q) | Q(content__icontains=q) | Q(keywords__icontains=q)
        )
    if category:
        queryset = queryset.filter(category=category)
    if website:
        queryset = queryset.filter(Q(website=website) | Q(website__isnull=True))
    return queryset


def knowledge_for_website(website):
    """Active articles the AI may use for `website`, in the order they are offered.

    This website's own articles come before the ones shared by every website,
    then by category priority, so a site can override a general policy.
    """
    queryset = KnowledgeArticle.objects.filter(is_active=True)
    if website is not None:
        queryset = queryset.filter(Q(website=website) | Q(website__isnull=True))
    else:
        queryset = queryset.filter(website__isnull=True)
    rank = {category: index for index, category in enumerate(KNOWLEDGE_PRIORITY)}
    return sorted(
        queryset,
        key=lambda article: (
            0 if article.website_id else 1,
            rank.get(article.category, len(rank)),
            article.title.lower(),
            article.pk,
        ),
    )
