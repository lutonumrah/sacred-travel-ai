"""Aggregations behind the overview, reports and analytics pages."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from bookings.models import Booking, BookingStatus, Payment, PaymentStatus
from conversations.models import (
    Conversation,
    ConversationHandoff,
    ConversationStatus,
    Message,
    MessageSender,
)
from core.attribution import campaign_key, campaign_label
from crm.models import FollowUpTask, Lead, LeadStatus
from inventory.models import CarRental, Hotel, TourPackage


def date_range(days=30):
    end = timezone.localdate()
    return end - timedelta(days=days - 1), end


def period(days=30, start=None, end=None):
    """`(start, end)` dates, inclusive: explicit bounds win, else the last `days` days."""
    end = end or timezone.localdate()
    start = start or end - timedelta(days=days - 1)
    if start > end:
        start, end = end, start
    return start, end


def _in_period(queryset, field, start, end):
    return queryset.filter(**{f"{field}__date__gte": start, f"{field}__date__lte": end})


def _rate(part, whole):
    return round((part / whole) * 100, 1) if whole else 0.0


# Pipeline positions that mean the lead was qualified at some point. Follow-up
# and Lost leads count only if their history shows one of these.
QUALIFIED_STATUSES = (
    LeadStatus.QUALIFIED,
    LeadStatus.INTERESTED,
    LeadStatus.PAYMENT_PENDING,
    LeadStatus.CONVERTED,
)
PAID_BOOKING_STATUSES = (BookingStatus.CONFIRMED,)


def overview_kpis(*, website=None, days=30, start=None, end=None):
    """Headline numbers for the dashboard landing page and the reports page."""
    start, end = period(days, start, end)

    leads = Lead.objects.filter(is_deleted=False)
    bookings = Booking.objects.all()
    conversations = Conversation.objects.all()
    payments = Payment.objects.filter(status=PaymentStatus.SUCCESS)
    if website:
        leads = leads.filter(website=website)
        bookings = bookings.filter(website=website)
        conversations = conversations.filter(website=website)
        payments = payments.filter(booking__website=website)

    period_leads = _in_period(leads, "created_at", start, end)
    period_bookings = _in_period(bookings, "created_at", start, end)
    converted = period_leads.filter(status=LeadStatus.CONVERTED).count()
    total_leads = period_leads.count()

    revenue = (
        _in_period(payments, "paid_at", start, end).aggregate(total=Sum("amount"))["total"] or 0
    )

    return {
        "period_days": (end - start).days + 1,
        "start": start,
        "end": end,
        "leads_total": total_leads,
        "leads_new": period_leads.filter(status=LeadStatus.NEW).count(),
        "leads_converted": converted,
        "conversion_rate": _rate(converted, total_leads),
        "conversations_live": conversations.exclude(
            status=ConversationStatus.CLOSED
        ).count(),
        "conversations_waiting": conversations.filter(
            status=ConversationStatus.WAITING
        ).count(),
        "bookings_total": period_bookings.count(),
        "bookings_confirmed": period_bookings.filter(status=BookingStatus.CONFIRMED).count(),
        "bookings_pending": bookings.filter(status=BookingStatus.PENDING).count(),
        "revenue": revenue,
        "average_booking_value": bookings.filter(status=BookingStatus.CONFIRMED).aggregate(
            value=Avg("total_amount")
        )["value"]
        or 0,
        "follow_ups_overdue": FollowUpTask.objects.filter(
            is_completed=False, due_at__lt=timezone.now()
        ).count(),
        "inventory_counts": {
            "hotels": Hotel.objects.filter(is_deleted=False, is_active=True).count(),
            "cars": CarRental.objects.filter(is_deleted=False, is_active=True).count(),
            "packages": TourPackage.objects.filter(is_deleted=False, is_active=True).count(),
        },
    }


def _period_leads(*, days, start, end, website):
    start, end = period(days, start, end)
    queryset = _in_period(Lead.objects.filter(is_deleted=False), "created_at", start, end)
    if website:
        queryset = queryset.filter(website=website)
    return queryset


def leads_by_day(*, days=30, website=None, start=None, end=None):
    rows = (
        _period_leads(days=days, start=start, end=end, website=website)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("day")
    )
    return [{"day": row["day"], "total": row["total"]} for row in rows]


def leads_by_source(*, days=90, website=None, start=None, end=None):
    return list(
        _period_leads(days=days, start=start, end=end, website=website)
        .values("source")
        .annotate(total=Count("id"))
        .order_by("-total")
    )


def leads_by_status(*, website=None, start=None, end=None):
    """All leads by status; only those created between `start` and `end` if given."""
    queryset = Lead.objects.filter(is_deleted=False)
    if website:
        queryset = queryset.filter(website=website)
    if start or end:
        queryset = _in_period(queryset, "created_at", *period(start=start, end=end))
    return list(queryset.values("status").annotate(total=Count("id")).order_by("-total"))


def _period_payments(*, days, start, end, website):
    start, end = period(days, start, end)
    queryset = _in_period(
        Payment.objects.filter(status=PaymentStatus.SUCCESS), "paid_at", start, end
    )
    if website:
        queryset = queryset.filter(booking__website=website)
    return queryset


def revenue_by_day(*, days=30, website=None, start=None, end=None):
    rows = (
        _period_payments(days=days, start=start, end=end, website=website)
        .annotate(day=TruncDate("paid_at"))
        .values("day")
        .annotate(total=Sum("amount"))
        .order_by("day")
    )
    return [{"day": row["day"], "total": row["total"]} for row in rows]


def revenue_rows(*, days=30, website=None, start=None, end=None):
    """Settled payments by day, website and product type (the revenue CSV)."""
    return list(
        _period_payments(days=days, start=start, end=end, website=website)
        .annotate(day=TruncDate("paid_at"))
        .values("day", "booking__website__name", "booking__product_type", "currency")
        .annotate(payments=Count("id"), total=Sum("amount"))
        .order_by("day", "booking__website__name", "booking__product_type")
    )


def top_destinations(*, limit=10, days=90, website=None, start=None, end=None):
    return list(
        _period_leads(days=days, start=start, end=end, website=website)
        .exclude(destination="")
        .values("destination")
        .annotate(total=Count("id"))
        .order_by("-total")[:limit]
    )


def qualified_lead_ids(leads):
    """Ids among `leads` that are, or ever were, Qualified or further along."""
    from crm.models import LeadActivity

    reached = LeadActivity.objects.filter(
        lead__in=leads.values("pk"),
        activity_type="status_change",
        details__to__in=[str(status) for status in QUALIFIED_STATUSES],
    ).values("lead_id")
    return set(
        leads.filter(Q(status__in=QUALIFIED_STATUSES) | Q(pk__in=reached)).values_list(
            "pk", flat=True
        )
    )


FUNNEL_GROUPS = ("website", "source", "campaign")


def conversion_funnel(
    *, days=30, start=None, end=None, website=None, group_by=FUNNEL_GROUPS
):
    """Chats → leads → qualified → bookings → paid → revenue, grouped.

    A cohort report: leads created in the period, and the bookings and
    payments those leads went on to make (whenever they happened). Chats are
    conversations started in the period; they only exist for the AI-chat source.
    `group_by` takes any of "website", "source" and "campaign" (UTM source /
    medium / campaign).
    """
    from crm.models import LeadSource

    start, end = period(days, start, end)
    leads = _period_leads(days=days, start=start, end=end, website=website)
    chats = _in_period(Conversation.objects.all(), "created_at", start, end)
    if website:
        chats = chats.filter(website=website)
    source_labels = dict(LeadSource.choices)
    fields = ("website_id", "website__name", "utm_source", "utm_medium", "utm_campaign")

    def key_for(row, source):
        key = []
        if "website" in group_by:
            key.append(("website", row["website__name"] or "(no website)"))
        if "source" in group_by:
            key.append(("source", source))
        if "campaign" in group_by:
            key.append(("campaign", campaign_key(row)))
        return tuple(key)

    rows = {}

    def row_for(key):
        if key not in rows:
            parts = dict(key)
            campaign = parts.get("campaign", ("", "", ""))
            source = parts.get("source")
            rows[key] = {
                "website": parts.get("website", ""),
                "source": source or "",
                "source_label": source_labels.get(source, source or ""),
                "campaign": campaign_label(campaign) if "campaign" in parts else "",
                "utm_source": campaign[0],
                "utm_medium": campaign[1],
                "utm_campaign": campaign[2],
                # Only AI-chat leads come from chats; other sources have none.
                "chats": 0 if source in (None, LeadSource.AI_CHAT) else None,
                "chats_with_lead": 0,
                "leads": 0,
                "qualified": 0,
                "bookings": 0,
                "paid": 0,
                "revenue": Decimal("0"),
            }
        return rows[key]

    for chat in chats.values("lead_id", *fields):
        row = row_for(key_for(chat, LeadSource.AI_CHAT))
        row["chats"] += 1
        row["chats_with_lead"] += chat["lead_id"] is not None

    qualified = qualified_lead_ids(leads)
    bookings = {}
    for booking in Booking.objects.filter(lead__in=leads.values("pk")).values("lead_id", "status"):
        counts = bookings.setdefault(booking["lead_id"], [0, 0])
        counts[0] += 1
        if booking["status"] in PAID_BOOKING_STATUSES:
            counts[1] += 1
    revenue = dict(
        Payment.objects.filter(status=PaymentStatus.SUCCESS, booking__lead__in=leads.values("pk"))
        .values("booking__lead_id")
        .annotate(total=Sum("amount"))
        .values_list("booking__lead_id", "total")
    )
    for lead in leads.values("pk", "source", *fields):
        row = row_for(key_for(lead, lead["source"]))
        row["leads"] += 1
        row["qualified"] += lead["pk"] in qualified
        booked, paid = bookings.get(lead["pk"], (0, 0))
        row["bookings"] += booked
        row["paid"] += paid
        row["revenue"] += revenue.get(lead["pk"]) or 0

    result = list(rows.values())
    for row in result:
        # Share of chats that produced a lead (not leads / chats: some AI-chat
        # leads predate the period's chats, which would push it past 100%).
        row["chat_to_lead_rate"] = (
            _rate(row["chats_with_lead"], row["chats"]) if row["chats"] else None
        )
        row["lead_to_paid_rate"] = _rate(row["paid"], row["leads"])
    return sorted(
        result,
        key=lambda row: (-row["revenue"], -row["leads"], -(row["chats"] or 0), row["website"]),
    )


def website_performance(*, days=90, start=None, end=None):
    """Per-website funnel for the period: chats → leads → bookings → revenue."""
    from websites.selectors import list_websites

    start, end = period(days, start, end)
    rows = []
    for website in list_websites():
        leads = _in_period(
            Lead.objects.filter(is_deleted=False, website=website), "created_at", start, end
        )
        chats = _in_period(Conversation.objects.filter(website=website), "created_at", start, end)
        bookings = _in_period(Booking.objects.filter(website=website), "created_at", start, end)
        total = leads.count()
        chat_count = chats.count()
        chats_with_lead = chats.filter(lead__isnull=False).count()
        converted = leads.filter(status=LeadStatus.CONVERTED).count()
        revenue = (
            _period_payments(days=days, start=start, end=end, website=website).aggregate(
                total=Sum("amount")
            )["total"]
            or 0
        )
        rows.append(
            {
                "website": website,
                "conversations": chat_count,
                "chats_with_lead": chats_with_lead,
                "chat_to_lead_rate": _rate(chats_with_lead, chat_count),
                "leads": total,
                "converted": converted,
                "conversion_rate": _rate(converted, total),
                "bookings": bookings.count(),
                "paid_bookings": bookings.filter(status__in=PAID_BOOKING_STATUSES).count(),
                "revenue": revenue,
            }
        )
    return sorted(rows, key=lambda row: row["revenue"], reverse=True)


# How `first_response_seconds` is measured, shown on the analytics page and API.
FIRST_RESPONSE_DEFINITION = (
    "For each chat the person took over in the period: from when the customer "
    "started waiting (the handoff request or their first unanswered message, "
    "whichever came first) to that person's first reply. Takeovers they never "
    "replied to are left out."
)


def first_response_seconds(user, *, start, end):
    """Seconds the customer waited for `user`'s first reply, per taken-over chat.

    See FIRST_RESPONSE_DEFINITION. Derived from message timestamps only.
    """
    times = []
    handoffs = _in_period(
        ConversationHandoff.objects.filter(taken_by=user), "created_at", start, end
    ).values_list("conversation_id", "created_at", "resumed_ai_at")
    for conversation_id, taken_at, resumed_at in handoffs[:500]:
        messages = Message.objects.filter(conversation_id=conversation_id, is_internal=False)
        replies = messages.filter(
            sender_type=MessageSender.AGENT, sender_user=user, created_at__gte=taken_at
        )
        if resumed_at:
            replies = replies.filter(created_at__lte=resumed_at)
        replied_at = replies.order_by("created_at").values_list("created_at", flat=True).first()
        if replied_at is None:
            continue
        answered_at = (
            messages.filter(
                sender_type__in=(MessageSender.AI, MessageSender.AGENT), created_at__lt=taken_at
            )
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        waiting = messages.filter(created_at__lte=taken_at).filter(
            Q(sender_type=MessageSender.CUSTOMER)
            | Q(sender_type=MessageSender.SYSTEM, metadata__has_key="reason")
        )
        if answered_at:
            waiting = waiting.filter(created_at__gt=answered_at)
        since = waiting.order_by("created_at").values_list("created_at", flat=True).first()
        times.append(max(0.0, (replied_at - (since or taken_at)).total_seconds()))
    return times


def employee_performance(*, days=90, start=None, end=None):
    """Per-agent lead handling, chat takeovers, response time and conversion."""
    from accounts.selectors import assignable_users

    start, end = period(days, start, end)
    rows = []
    for user in assignable_users():
        leads = _in_period(
            Lead.objects.filter(is_deleted=False, assigned_to=user), "created_at", start, end
        )
        total = leads.count()
        converted = leads.filter(status=LeadStatus.CONVERTED).count()
        responses = first_response_seconds(user, start=start, end=end)
        rows.append(
            {
                "user": user,
                "leads": total,
                "converted": converted,
                "conversion_rate": _rate(converted, total),
                "open_follow_ups": FollowUpTask.objects.filter(
                    assigned_to=user, is_completed=False
                ).count(),
                "conversations": _in_period(
                    Conversation.objects.filter(assigned_to=user), "created_at", start, end
                ).count(),
                "handoffs": _in_period(
                    ConversationHandoff.objects.filter(taken_by=user), "created_at", start, end
                ).count(),
                "responded_handoffs": len(responses),
                "avg_first_response_seconds": (
                    round(sum(responses) / len(responses)) if responses else None
                ),
                "revenue": _in_period(
                    Payment.objects.filter(
                        status=PaymentStatus.SUCCESS, booking__lead__assigned_to=user
                    ),
                    "paid_at",
                    start,
                    end,
                ).aggregate(total=Sum("amount"))["total"]
                or 0,
            }
        )
    return sorted(rows, key=lambda row: row["converted"], reverse=True)


def booking_report(*, days=30, website=None, start=None, end=None):
    start, end = period(days, start, end)
    bookings = _in_period(Booking.objects.all(), "created_at", start, end)
    if website:
        bookings = bookings.filter(website=website)
    by_product = list(
        bookings.values("product_type")
        .annotate(total=Count("id"), revenue=Sum("total_amount"))
        .order_by("-total")
    )
    by_status = list(
        bookings.values("status").annotate(total=Count("id")).order_by("-total")
    )
    return {"by_product": by_product, "by_status": by_status, "total": bookings.count()}


def recent_activity(limit=10, user=None):
    """Latest records, limited to what `user` may open (everything when no user)."""
    leads = Lead.objects.filter(is_deleted=False)
    bookings = Booking.objects.all()
    conversations = Conversation.objects.all()
    if user is not None:
        from bookings.selectors import visible_bookings
        from conversations.selectors import visible_conversations
        from core.mixins import SALES_ROLES
        from crm.selectors import visible_leads

        if not (user.is_superuser or user.role in SALES_ROLES):
            leads, bookings, conversations = (
                leads.none(),
                bookings.none(),
                conversations.none(),
            )
        else:
            leads = visible_leads(user)
            bookings = visible_bookings(user)
            conversations = visible_conversations(user)
    return {
        "leads": leads.select_related("customer")[:limit],
        "bookings": bookings.select_related("customer")[:limit],
        "conversations": conversations.select_related("customer", "website").exclude(
            status=ConversationStatus.CLOSED
        )[:limit],
    }
