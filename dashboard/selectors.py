"""Aggregations behind the overview, reports and analytics pages."""

from datetime import timedelta

from django.db.models import Avg, Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from bookings.models import Booking, BookingStatus, Payment, PaymentStatus
from conversations.models import Conversation, ConversationStatus
from crm.models import FollowUpTask, Lead, LeadStatus
from inventory.models import CarRental, Hotel, TourPackage


def date_range(days=30):
    end = timezone.localdate()
    return end - timedelta(days=days - 1), end


def overview_kpis(*, website=None, days=30):
    """Headline numbers for the dashboard landing page."""
    start, end = date_range(days)

    leads = Lead.objects.filter(is_deleted=False)
    bookings = Booking.objects.all()
    conversations = Conversation.objects.all()
    if website:
        leads = leads.filter(website=website)
        bookings = bookings.filter(website=website)
        conversations = conversations.filter(website=website)

    period_leads = leads.filter(created_at__date__gte=start)
    converted = period_leads.filter(status=LeadStatus.CONVERTED).count()
    total_leads = period_leads.count()

    revenue = (
        Payment.objects.filter(status=PaymentStatus.SUCCESS, paid_at__date__gte=start)
        .aggregate(total=Sum("amount"))["total"]
        or 0
    )

    return {
        "period_days": days,
        "start": start,
        "end": end,
        "leads_total": total_leads,
        "leads_new": period_leads.filter(status=LeadStatus.NEW).count(),
        "leads_converted": converted,
        "conversion_rate": round((converted / total_leads) * 100, 1) if total_leads else 0.0,
        "conversations_live": conversations.exclude(
            status=ConversationStatus.CLOSED
        ).count(),
        "conversations_waiting": conversations.filter(
            status=ConversationStatus.WAITING
        ).count(),
        "bookings_total": bookings.filter(created_at__date__gte=start).count(),
        "bookings_confirmed": bookings.filter(
            status=BookingStatus.CONFIRMED, created_at__date__gte=start
        ).count(),
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


def leads_by_day(*, days=30, website=None):
    start, _end = date_range(days)
    queryset = Lead.objects.filter(is_deleted=False, created_at__date__gte=start)
    if website:
        queryset = queryset.filter(website=website)
    rows = (
        queryset.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("day")
    )
    return [{"day": row["day"], "total": row["total"]} for row in rows]


def leads_by_source(*, days=90, website=None):
    start, _end = date_range(days)
    queryset = Lead.objects.filter(is_deleted=False, created_at__date__gte=start)
    if website:
        queryset = queryset.filter(website=website)
    return list(
        queryset.values("source").annotate(total=Count("id")).order_by("-total")
    )


def leads_by_status(*, website=None):
    queryset = Lead.objects.filter(is_deleted=False)
    if website:
        queryset = queryset.filter(website=website)
    return list(queryset.values("status").annotate(total=Count("id")).order_by("-total"))


def revenue_by_day(*, days=30):
    start, _end = date_range(days)
    rows = (
        Payment.objects.filter(status=PaymentStatus.SUCCESS, paid_at__date__gte=start)
        .annotate(day=TruncDate("paid_at"))
        .values("day")
        .annotate(total=Sum("amount"))
        .order_by("day")
    )
    return [{"day": row["day"], "total": row["total"]} for row in rows]


def top_destinations(*, limit=10, days=90):
    start, _end = date_range(days)
    return list(
        Lead.objects.filter(is_deleted=False, created_at__date__gte=start)
        .exclude(destination="")
        .values("destination")
        .annotate(total=Count("id"))
        .order_by("-total")[:limit]
    )


def website_performance(*, days=90):
    """Per-website lead, conversion and revenue table."""
    from websites.selectors import list_websites

    start, _end = date_range(days)
    rows = []
    for website in list_websites():
        leads = Lead.objects.filter(
            is_deleted=False, website=website, created_at__date__gte=start
        )
        total = leads.count()
        converted = leads.filter(status=LeadStatus.CONVERTED).count()
        revenue = (
            Payment.objects.filter(
                status=PaymentStatus.SUCCESS,
                booking__website=website,
                paid_at__date__gte=start,
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        )
        rows.append(
            {
                "website": website,
                "leads": total,
                "converted": converted,
                "conversion_rate": round((converted / total) * 100, 1) if total else 0.0,
                "conversations": Conversation.objects.filter(website=website).count(),
                "bookings": Booking.objects.filter(website=website).count(),
                "revenue": revenue,
            }
        )
    return sorted(rows, key=lambda row: row["revenue"], reverse=True)


def employee_performance(*, days=90):
    """Per-agent lead handling and conversion."""
    from accounts.selectors import assignable_users

    start, _end = date_range(days)
    rows = []
    for user in assignable_users():
        leads = Lead.objects.filter(
            is_deleted=False, assigned_to=user, created_at__date__gte=start
        )
        total = leads.count()
        converted = leads.filter(status=LeadStatus.CONVERTED).count()
        rows.append(
            {
                "user": user,
                "leads": total,
                "converted": converted,
                "conversion_rate": round((converted / total) * 100, 1) if total else 0.0,
                "open_follow_ups": FollowUpTask.objects.filter(
                    assigned_to=user, is_completed=False
                ).count(),
                "conversations": Conversation.objects.filter(assigned_to=user).count(),
                "revenue": Payment.objects.filter(
                    status=PaymentStatus.SUCCESS,
                    booking__lead__assigned_to=user,
                    paid_at__date__gte=start,
                ).aggregate(total=Sum("amount"))["total"]
                or 0,
            }
        )
    return sorted(rows, key=lambda row: row["converted"], reverse=True)


def booking_report(*, days=30):
    start, _end = date_range(days)
    bookings = Booking.objects.filter(created_at__date__gte=start)
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
