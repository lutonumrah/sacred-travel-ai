"""Notification helper.

The `Notification` model lives in `bookings`, which already imports `crm` and
`conversations`. Resolving the model lazily lets every module raise
notifications without creating an import cycle.
"""

from django.apps import apps


def _model():
    return apps.get_model("bookings", "Notification")


def notify(*, recipient, notification_type, title, body="", link="", metadata=None):
    if recipient is None:
        return None
    return _model().objects.create(
        recipient=recipient,
        notification_type=notification_type,
        title=title,
        body=body,
        link=link,
        metadata=metadata or {},
    )


def notify_many(*, recipients, notification_type, title, body="", link="", metadata=None):
    model = _model()
    rows = [
        model(
            recipient=recipient,
            notification_type=notification_type,
            title=title,
            body=body,
            link=link,
            metadata=metadata or {},
        )
        for recipient in recipients
        if recipient is not None
    ]
    return model.objects.bulk_create(rows)


def notify_managers(*, notification_type, title, body="", link="", metadata=None, exclude=None):
    """Fan a notification out to every active admin and manager."""
    user_model = apps.get_model("accounts", "User")
    recipients = user_model.objects.filter(
        is_active=True, is_active_employee=True
    ).filter(role__in=["admin", "manager"])
    if exclude is not None:
        recipients = recipients.exclude(pk=exclude.pk)
    return notify_many(
        recipients=list(recipients),
        notification_type=notification_type,
        title=title,
        body=body,
        link=link,
        metadata=metadata,
    )
