from django.utils import timezone

from bookings.models import Notification


def mark_read(*, user, notification_id=None):
    """Mark one notification read, or every notification for the user."""
    queryset = Notification.objects.filter(recipient=user, is_read=False)
    if notification_id is not None:
        queryset = queryset.filter(pk=notification_id)
    return queryset.update(is_read=True)


def follow_up_reminders_due(*, user=None):
    """Follow-ups whose reminder time has passed and are still open."""
    from crm.models import FollowUpTask

    queryset = FollowUpTask.objects.filter(
        is_completed=False, reminder_at__isnull=False, reminder_at__lte=timezone.now()
    ).select_related("lead", "assigned_to")
    if user is not None:
        queryset = queryset.filter(assigned_to=user)
    return queryset
