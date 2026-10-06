from django.db.models import Count, Max, Q

from .models import Conversation, ConversationStatus


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


def waiting_count():
    return Conversation.objects.filter(status=ConversationStatus.WAITING).count()


def conversation_messages(conversation, *, include_internal=True):
    queryset = conversation.messages.select_related("sender_user")
    if not include_internal:
        queryset = queryset.filter(is_internal=False)
    return queryset
