"""Query helpers shared by the dashboard views and the API."""

from django.core.paginator import Paginator


def paginate(queryset, page_number, per_page=25):
    """Return a page object, clamping bad page numbers to the first page."""
    paginator = Paginator(queryset, per_page)
    try:
        return paginator.page(int(page_number or 1))
    except (ValueError, TypeError):
        return paginator.page(1)
    except Exception:
        return paginator.page(paginator.num_pages)


def apply_ordering(queryset, requested, allowed, default):
    """Only allow ordering by a whitelisted set of fields."""
    field = (requested or "").strip()
    if field.lstrip("-") in allowed:
        return queryset.order_by(field)
    return queryset.order_by(default)
