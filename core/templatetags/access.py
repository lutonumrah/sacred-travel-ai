from django import template

from core import access

register = template.Library()


@register.filter
def can_open(user, url_name):
    """`{% if user|can_open:"bookings:cancel" %}` — the view's own role check."""
    return access.can_open(user, url_name)
