"""Per-IP rate limits for the anonymous public endpoints (chat widget, pay links).

Counters live in the default cache. That is LocMem unless CACHES says otherwise,
so each gunicorn worker counts on its own: with two workers a client can get up
to twice the configured rate. Good enough to stop a runaway script or a bored
visitor without adding Redis; tighten it with a shared cache if that changes.

The client IP comes from DRF's `get_ident`, which with NUM_PROXIES=1 takes the
address nginx appended to X-Forwarded-For — anything a client writes there
itself sits further left and is ignored.
"""

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle


class PublicRateThrottle(AnonRateThrottle):
    """Reads its rate from PUBLIC_API_THROTTLE_RATES at request time, not import time."""

    def get_rate(self):
        return getattr(settings, "PUBLIC_API_THROTTLE_RATES", {}).get(self.scope)


class WidgetChatThrottle(PublicRateThrottle):
    scope = "widget_chat"


class WidgetPollThrottle(PublicRateThrottle):
    scope = "widget_poll"


class WidgetBookThrottle(PublicRateThrottle):
    scope = "widget_book"


class PublicPayThrottle(PublicRateThrottle):
    scope = "public_pay"
