from django import template

register = template.Library()


@register.filter
def duration_short(seconds):
    """`95` → `1m 35s`, `7300` → `2h 1m`. Blank for None."""
    if seconds is None:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"
