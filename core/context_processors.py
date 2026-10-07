def nav_context(request):
    """Shared branding for every page."""
    return {"app_name": "Scared Travel AI"}


def notification_badge(request):
    """Unread notification and waiting-chat counts for the sidebar."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"unread_notifications": 0, "waiting_chats": 0}

    from bookings.selectors import unread_notifications
    from conversations.selectors import waiting_count

    return {
        "unread_notifications": unread_notifications(user).count(),
        "waiting_chats": waiting_count(user),
    }
