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


def list_customers(*, q="", city=""):
    queryset = Customer.objects.filter(is_deleted=False)
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
    user=None,
):
    queryset = Lead.objects.filter(is_deleted=False).select_related(
        "customer", "website", "assigned_to", "assigned_team"
    )
    if q:
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
    if created_from:
        queryset = queryset.filter(created_at__date__gte=created_from)
    if created_to:
        queryset = queryset.filter(created_at__date__lte=created_to)
    if user is not None and not (user.is_superuser or user.is_manager):
        # Employees only see what is theirs or unassigned.
        queryset = queryset.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))
    return queryset


def pipeline_columns(*, user=None, website=None):
    """Leads bucketed by status, in pipeline order, for the kanban board."""
    leads = list_leads(user=user, website=website)
    buckets = {status: [] for status in PIPELINE_ORDER}
    for lead in leads[:500]:
        buckets.setdefault(lead.status, []).append(lead)
    return [
        {
            "status": status,
            "label": LeadStatus(status).label,
            "leads": buckets.get(status, []),
            "count": len(buckets.get(status, [])),
        }
        for status in PIPELINE_ORDER
    ]


def follow_up_buckets(*, user=None, include_completed=False):
    queryset = FollowUpTask.objects.select_related("lead", "lead__customer", "assigned_to")
    if not include_completed:
        queryset = queryset.filter(is_completed=False)
    if user is not None and not (user.is_superuser or user.is_manager):
        queryset = queryset.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))

    now = timezone.now()
    end_of_today = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    return {
        "overdue": queryset.filter(is_completed=False, due_at__lt=now),
        "today": queryset.filter(due_at__gte=now, due_at__lte=end_of_today),
        "upcoming": queryset.filter(due_at__gt=end_of_today),
        "completed": (
            FollowUpTask.objects.filter(is_completed=True).select_related("lead")[:25]
            if include_completed
            else FollowUpTask.objects.none()
        ),
    }


def lead_status_counts(*, website=None):
    queryset = Lead.objects.filter(is_deleted=False)
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
