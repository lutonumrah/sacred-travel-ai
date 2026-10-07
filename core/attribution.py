"""Source attribution: which website and campaign a lead or booking came from.

The widget and the website-form intake send UTM tags plus the referring and
landing page. All of it is visitor-controlled, so it is trimmed, stripped of
control characters and cut to the column size; URLs must be http(s).
"""

import re
from urllib.parse import urlsplit

ATTRIBUTION_FIELDS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "referrer",
    "landing_page",
)
URL_FIELDS = {"referrer", "landing_page"}
MAX_LENGTHS = {
    "utm_source": 100,
    "utm_medium": 100,
    "utm_campaign": 150,
    "utm_term": 150,
    "utm_content": 150,
    "referrer": 500,
    "landing_page": 500,
}
LABELS = {
    "utm_source": "Source (utm_source)",
    "utm_medium": "Medium (utm_medium)",
    "utm_campaign": "Campaign (utm_campaign)",
    "utm_term": "Term (utm_term)",
    "utm_content": "Content (utm_content)",
    "referrer": "Referrer",
    "landing_page": "Landing page",
}

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _clean_value(name, value):
    if not isinstance(value, (str, int, float)):
        return ""
    value = _CONTROL.sub("", str(value)).strip()
    if name in URL_FIELDS and value:
        try:
            parts = urlsplit(value)
        except ValueError:
            return ""
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return ""
    return value[: MAX_LENGTHS[name]]


def clean_attribution(data):
    """The known attribution keys from `data`, validated; blanks dropped."""
    if not hasattr(data, "get"):
        return {}
    cleaned = {}
    for name in ATTRIBUTION_FIELDS:
        value = _clean_value(name, data.get(name))
        if value:
            cleaned[name] = value
    return cleaned


def has_attribution(obj):
    return any(getattr(obj, name, "") for name in ATTRIBUTION_FIELDS)


def apply_attribution(obj, data):
    """Set cleaned attribution on `obj` unless it already has some. Returns changed fields.

    First touch wins: a visitor who later comes back through another campaign
    keeps the credit with the one that brought them in.
    """
    cleaned = clean_attribution(data)
    if not cleaned or has_attribution(obj):
        return []
    for name, value in cleaned.items():
        setattr(obj, name, value)
    return list(cleaned)


def copy_attribution(source, target):
    """Copy attribution from one record to another that has none. Returns changed fields."""
    if source is None or has_attribution(target):
        return []
    return apply_attribution(
        target, {name: getattr(source, name, "") for name in ATTRIBUTION_FIELDS}
    )


def attribution_rows(obj):
    """`[(label, value)]` for the detail pages, blanks skipped."""
    return [
        (LABELS[name], getattr(obj, name))
        for name in ATTRIBUTION_FIELDS
        if getattr(obj, name, "")
    ]


def campaign_key(row):
    """(source, medium, campaign) from a model or a values() dict."""
    get = row.get if isinstance(row, dict) else (lambda name: getattr(row, name, ""))
    return (get("utm_source") or "", get("utm_medium") or "", get("utm_campaign") or "")


def campaign_label(key):
    source, medium, campaign = key
    if not any(key):
        return "(no campaign)"
    return " / ".join(part or "—" for part in (source, medium, campaign))
