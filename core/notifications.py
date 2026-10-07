"""Notification helper.

The `Notification` model lives in `bookings`, which already imports `crm` and
`conversations`. Resolving the model lazily lets every module raise
notifications without creating an import cycle.

Each notification is also emailed to recipients who have `email_notifications`
on and an address, once the surrounding transaction commits. `email=False`
keeps a notification in-app only; by default every type except "system" is
mailed (see EMAILED_TYPES). Mail failures are logged and never reach callers.
"""

import logging

from django.apps import apps
from django.db.models import Q
from django.urls import reverse

logger = logging.getLogger(__name__)

# "system" rows are housekeeping; the other four are what the FIP asks to be
# delivered. Per-message chat pings and routine pipeline moves opt out at the
# call site with email=False.
EMAILED_TYPES = {"lead", "handoff", "payment", "follow_up"}


def _model():
    return apps.get_model("bookings", "Notification")


def _email_recipients(recipients):
    return [
        user
        for user in recipients
        if user is not None
        and user.is_active
        and getattr(user, "email_notifications", False)
        and user.email
    ]


def _queue_emails(*, recipients, notification_type, title, body, link, email):
    if email is False or (email is None and notification_type not in EMAILED_TYPES):
        return
    people = _email_recipients(recipients)
    if not people:
        return
    try:
        from .emails import absolute_url, send_email_on_commit

        type_label = dict(_model()._meta.get_field("notification_type").choices).get(
            notification_type, notification_type.replace("_", " ").title()
        )
        context = {
            "title": title,
            "body": body,
            "url": absolute_url(link) if link else "",
            "type_label": type_label,
            "prefs_url": absolute_url(reverse("accounts:password_change")),
        }
        for user in people:
            send_email_on_commit(
                to=user.email, template="staff_notification", context=dict(context, user=user)
            )
    except Exception:
        # Queuing is local work, but a notification must never fail its caller.
        logger.exception("Could not queue notification emails for %r.", title)


def notify(*, recipient, notification_type, title, body="", link="", metadata=None, email=None):
    if recipient is None:
        return None
    row = _model().objects.create(
        recipient=recipient,
        notification_type=notification_type,
        title=title,
        body=body,
        link=link,
        metadata=metadata or {},
    )
    _queue_emails(
        recipients=[recipient],
        notification_type=notification_type,
        title=title,
        body=body,
        link=link,
        email=email,
    )
    return row


def notify_many(
    *, recipients, notification_type, title, body="", link="", metadata=None, email=None
):
    model = _model()
    recipients = [recipient for recipient in recipients if recipient is not None]
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
    ]
    created = model.objects.bulk_create(rows)
    _queue_emails(
        recipients=recipients,
        notification_type=notification_type,
        title=title,
        body=body,
        link=link,
        email=email,
    )
    return created


def notify_managers(
    *, notification_type, title, body="", link="", metadata=None, exclude=None, email=None
):
    """Fan a notification out to every active admin and manager."""
    user_model = apps.get_model("accounts", "User")
    recipients = user_model.objects.filter(
        is_active=True, is_active_employee=True
    ).filter(
        # `createsuperuser` leaves the role at its default; superusers see
        # everything a manager does, so they get the manager alerts too.
        Q(role__in=["admin", "manager"]) | Q(is_superuser=True)
    )
    if exclude is not None:
        recipients = recipients.exclude(pk=exclude.pk)
    return notify_many(
        recipients=list(recipients),
        notification_type=notification_type,
        title=title,
        body=body,
        link=link,
        metadata=metadata,
        email=email,
    )
