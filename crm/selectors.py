import re

from django.db.models import Count, Q
from django.utils import timezone

from .models import Customer, FollowUpTask, Lead, LeadStatus

PIPELINE_ORDER = [
    LeadStatus.NEW,
    LeadStatus.QUALIFIED,
    LeadStatus.INTERESTED,
    LeadStatus.PAYMENT_PENDING,
    LeadStatus.CONVERTED,
    LeadStatus.FOLLOW_UP,
    LeadStatus.LOST,
]


# `ENQ-000123`, the reference a website-form visitor is given (Lead.reference).
REFERENCE_RE = re.compile(r"ENQ-0*(\d{1,10})", re.I)


# Travel preferences the chat extracts, in display order.
PREFERENCE_LABELS = (
    ("hotel_stars", "Hotel stars"),
    ("amenities", "Amenities"),
    ("food", "Food"),
    ("car_type", "Car type"),
    ("transmission", "Transmission"),
    ("trip_style", "Trip style"),
    ("product_type", "Looking for"),
    ("nights", "Nights"),
    ("notes", "Notes"),
)


def preference_rows(preferences):
    """`[(label, text)]` for the known preferences, skipping blanks."""
    preferences = preferences or {}
    rows = []
    for key, label in PREFERENCE_LABELS:
        value = preferences.get(key)
        if value in (None, "", 0, [], {}):
            continue
        if key == "hotel_stars":
            value = f"{value}-star or better"
        elif isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        rows.append((label, value))
    return rows


def sees_everything(user):
    return user.is_superuser or user.is_manager


def visible_leads(user):
    """Managers see every lead; employees their own, unassigned ones and their teams'.

    "Unassigned" means no owner and no team.

    A lead handed to a team (with or without a named owner) is visible to every
    member of that team, and with it its follow-ups and bookings.
    """
    queryset = Lead.objects.filter(is_deleted=False)
    if not sees_everything(user):
        from accounts.models import Team

        queryset = queryset.filter(
            Q(assigned_to=user)
            # Unassigned means nobody at all: a lead handed to a team without a
            # named owner belongs to that team, not to every employee.
            | Q(assigned_to__isnull=True, assigned_team__isnull=True)
            | Q(assigned_team__in=Team.objects.filter(members=user).values("pk"))
        )
    return queryset


def visible_follow_ups(user):
    queryset = FollowUpTask.objects.all()
    if not sees_everything(user):
        queryset = queryset.filter(
            Q(assigned_to=user) | Q(lead__in=visible_leads(user).values("pk"))
        )
    return queryset


def visible_customers(user, *, include_archived=False):
    """A customer is visible through any lead, chat or booking the user can see.

    Customers with no history at all are visible too, so an employee can open the
    profile they just created before any lead exists. Archived customers are
    left out unless asked for; only managers archive or restore them.
    """
    queryset = Customer.objects.all()
    if not include_archived:
        queryset = queryset.filter(is_deleted=False)
    if sees_everything(user):
        return queryset
    # Imported lazily: both modules import crm models.
    from bookings.selectors import visible_bookings
    from conversations.selectors import visible_conversations

    return queryset.filter(
        Q(pk__in=visible_leads(user).values("customer_id"))
        | Q(pk__in=visible_conversations(user).values("customer_id"))
        | Q(pk__in=visible_bookings(user).values("customer_id"))
        | Q(leads__isnull=True, conversations__isnull=True, bookings__isnull=True)
    ).distinct()


def list_customers(*, q="", city="", user=None, include_archived=False):
    if user is not None:
        queryset = visible_customers(user, include_archived=include_archived)
    else:
        queryset = Customer.objects.all()
        if not include_archived:
            queryset = queryset.filter(is_deleted=False)
    if q:
        queryset = queryset.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(whatsapp__icontains=q)
        )
    if city:
        queryset = queryset.filter(city__icontains=city)
    return queryset.annotate(lead_count=Count("leads", distinct=True)).order_by("-created_at")


def list_leads(
    *,
    q="",
    status="",
    source="",
    destination="",
    assigned_to=None,
    website=None,
    created_from=None,
    created_to=None,
    team=None,
    user=None,
):
    queryset = (
        visible_leads(user) if user is not None else Lead.objects.filter(is_deleted=False)
    ).select_related("customer", "website", "assigned_to", "assigned_team")
    if q:
        reference = REFERENCE_RE.fullmatch(q.strip())
        if reference:
            queryset = queryset.filter(pk=int(reference.group(1)))
        else:
            queryset = queryset.filter(
                Q(title__icontains=q)
                | Q(destination__icontains=q)
                | Q(customer__first_name__icontains=q)
                | Q(customer__last_name__icontains=q)
                | Q(customer__email__icontains=q)
                | Q(customer__phone__icontains=q)
            )
    if status:
        queryset = queryset.filter(status=status)
    if source:
        queryset = queryset.filter(source=source)
    if destination:
        queryset = queryset.filter(destination__icontains=destination)
    if assigned_to:
        queryset = queryset.filter(assigned_to=assigned_to)
    if website:
        queryset = queryset.filter(website=website)
    if team:
        queryset = queryset.filter(assigned_team=team)
    if created_from:
        queryset = queryset.filter(created_at__date__gte=created_from)
    if created_to:
        queryset = queryset.filter(created_at__date__lte=created_to)
    return queryset


PIPELINE_CARDS_PER_COLUMN = 25


def pipeline_columns(
    *, user=None, website=None, source="", assigned_to=None, team=None,
    limit=PIPELINE_CARDS_PER_COLUMN,
):
    """Leads bucketed by status, in pipeline order, for the kanban board.

    Each column holds its `limit` newest cards; `count` is the column's full
    size, so the board can link to the rest.
    """
    leads = list_leads(
        user=user, website=website, source=source, assigned_to=assigned_to, team=team
    )
    counts = dict(
        leads.order_by().values_list("status").annotate(total=Count("id"))
    )
    return [
        {
            "status": status,
            "label": LeadStatus(status).label,
            "leads": list(leads.filter(status=status)[:limit]) if counts.get(status) else [],
            "count": counts.get(status, 0),
            "more": max(0, counts.get(status, 0) - limit),
        }
        for status in PIPELINE_ORDER
    ]


def follow_up_buckets(*, user=None, include_completed=False):
    scoped = (
        visible_follow_ups(user) if user is not None else FollowUpTask.objects.all()
    ).select_related("lead", "lead__customer", "assigned_to")
    queryset = scoped if include_completed else scoped.filter(is_completed=False)

    now = timezone.now()
    # "Today" is the business's calendar day, not the UTC one.
    end_of_today = timezone.localtime(now).replace(
        hour=23, minute=59, second=59, microsecond=999999
    )
    return {
        "overdue": queryset.filter(is_completed=False, due_at__lt=now),
        "today": queryset.filter(due_at__gte=now, due_at__lte=end_of_today),
        "upcoming": queryset.filter(due_at__gt=end_of_today),
        "completed": (
            scoped.filter(is_completed=True).order_by("-completed_at", "-due_at")[:25]
            if include_completed
            else FollowUpTask.objects.none()
        ),
    }


def lead_status_counts(*, website=None, user=None):
    """Leads per pipeline status; with `user`, only leads that user can see."""
    queryset = visible_leads(user) if user is not None else Lead.objects.filter(is_deleted=False)
    if website:
        queryset = queryset.filter(website=website)
    counts = dict(
        queryset.values_list("status").annotate(total=Count("id")).values_list("status", "total")
    )
    return {status: counts.get(status, 0) for status in PIPELINE_ORDER}


def find_customer(*, email="", phone=""):
    """Match an existing customer on email or phone so chats don't duplicate people."""
    condition = Q()
    if email:
        condition |= Q(email__iexact=email)
    if phone:
        condition |= Q(phone=phone) | Q(whatsapp=phone)
    if not condition:
        return None
    return Customer.objects.filter(is_deleted=False).filter(condition).first()
