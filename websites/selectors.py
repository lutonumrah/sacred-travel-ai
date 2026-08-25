from django.db.models import Count, Q

from .models import Website, WebsiteAPIKey


def list_websites(*, q="", status=""):
    queryset = Website.objects.filter(is_deleted=False)
    if q:
        queryset = queryset.filter(
            Q(name__icontains=q)
            | Q(brand_name__icontains=q)
            | Q(domain__icontains=q)
            | Q(source_identifier__icontains=q)
        )
    if status == "active":
        queryset = queryset.filter(is_active=True)
    elif status == "inactive":
        queryset = queryset.filter(is_active=False)
    return queryset


def websites_with_counts():
    return (
        list_websites()
        .annotate(
            lead_count=Count("leads", distinct=True),
            conversation_count=Count("conversations", distinct=True),
            booking_count=Count("bookings", distinct=True),
        )
        .order_by("name")
    )


def active_key_for(public_key):
    """Resolve a widget public key to its active website."""
    return (
        WebsiteAPIKey.objects.select_related("website")
        .filter(
            public_key=public_key,
            is_active=True,
            website__is_active=True,
            website__is_deleted=False,
        )
        .first()
    )
