"""Outgoing email.

Every message is a template trio under `templates/emails/<name>/`:
`subject.txt`, `body.txt` and `body.html` (which extends `emails/base.html`).
`send_email` never raises: a mail failure is logged and reported as False so
the booking, payment or reset that triggered it still succeeds.

Callers inside a transaction use `send_email_on_commit`, so nothing is mailed
for work that rolls back and the SQLite write lock is not held while an SMTP
server answers.
"""

import logging
import re
from email.utils import formataddr, parseaddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

DEFAULT_BRAND = "Scared Travel"
DEFAULT_COLOR = "#0F766E"
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def brand_for(website=None):
    """(brand name, accent colour) for customer-facing pages and mail."""
    if website is None:
        return DEFAULT_BRAND, DEFAULT_COLOR
    # Goes into a style attribute: only a plain hex colour is allowed through.
    color = website.primary_color if HEX_COLOR.match(website.primary_color or "") else DEFAULT_COLOR
    return website.brand_name or website.name or DEFAULT_BRAND, color


def absolute_url(path):
    """`path` on the configured public site (settings.SITE_URL)."""
    if not path or path.startswith(("http://", "https://")):
        return path or ""
    base = getattr(settings, "SITE_URL", "").rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def _from_address(brand):
    """DEFAULT_FROM_EMAIL's mailbox, shown under the website's brand name."""
    _name, address = parseaddr(settings.DEFAULT_FROM_EMAIL)
    if not address:
        return settings.DEFAULT_FROM_EMAIL
    return formataddr((brand, address)) if brand else settings.DEFAULT_FROM_EMAIL


def render_email(template, context=None):
    """Return (subject, text, html) for `templates/emails/<template>/`."""
    ctx = {"brand": DEFAULT_BRAND, "brand_color": DEFAULT_COLOR, "site_url": settings.SITE_URL}
    ctx.update(context or {})
    subject = render_to_string(f"emails/{template}/subject.txt", ctx)
    # Header injection guard: a subject is one line, whatever the template did.
    subject = " ".join(subject.split())
    text = render_to_string(f"emails/{template}/body.txt", ctx)
    html = render_to_string(f"emails/{template}/body.html", ctx)
    return subject, text, html


def send_email(*, to, template, context=None, brand=None, reply_to=None):
    """Render and send one message. Returns True when the backend accepted it."""
    recipients = [to] if isinstance(to, str) else [addr for addr in (to or []) if addr]
    if not recipients:
        return False
    context = dict(context or {})
    if brand:
        context.setdefault("brand", brand)
    try:
        subject, text, html = render_email(template, context)
        message = EmailMultiAlternatives(
            subject=subject,
            body=text,
            from_email=_from_address(brand),
            to=recipients,
            reply_to=reply_to or None,
        )
        message.attach_alternative(html, "text/html")
        sent = message.send() > 0
    except Exception:
        logger.exception("Could not send %r email to %s.", template, ", ".join(recipients))
        return False
    logger.info("Sent %r email to %s.", template, ", ".join(recipients))
    return sent


def send_email_on_commit(*, on_sent=None, **kwargs):
    """Send once the surrounding transaction commits (immediately outside one).

    `on_sent(ok)` runs afterwards with the result, e.g. to record the attempt.
    """

    def deliver():
        ok = send_email(**kwargs)
        if on_sent is not None:
            try:
                on_sent(ok)
            except Exception:
                logger.exception("Recording the %r email failed.", kwargs.get("template"))

    transaction.on_commit(deliver, robust=True)
