"""Razorpay gateway wrapper.

Runs in one of two modes:

* **live** — `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` are set and the
  `razorpay` package is installed. Orders are created through Razorpay.
* **simulation** — anything missing. Orders are minted locally with the same
  shape and the same HMAC signature scheme, so the entire booking and
  verification flow can be developed and tested offline.

Signature verification is implemented directly with `hmac` in both modes: it is
the documented Razorpay algorithm and keeps verification working even when the
SDK is not installed.
"""

import hashlib
import hmac
import logging
import secrets

from django.conf import settings

logger = logging.getLogger(__name__)

SIMULATION_SECRET = "simulation-secret"


def key_id():
    return getattr(settings, "RAZORPAY_KEY_ID", "") or "rzp_test_simulation"


def _secret():
    return getattr(settings, "RAZORPAY_KEY_SECRET", "") or SIMULATION_SECRET


def _webhook_secret():
    return getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "") or SIMULATION_SECRET


def is_live():
    """True when real Razorpay credentials and the SDK are both available."""
    if not (getattr(settings, "RAZORPAY_KEY_ID", "") and getattr(settings, "RAZORPAY_KEY_SECRET", "")):
        return False
    try:
        import razorpay  # noqa: F401
    except ImportError:
        logger.warning("Razorpay keys are set but the `razorpay` package is not installed.")
        return False
    return True


def _client():
    import razorpay

    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def to_paise(amount):
    """Razorpay works in the smallest currency unit."""
    return int(round(float(amount) * 100))


def create_order(*, amount, currency="INR", receipt="", notes=None):
    """Create a payment order. Returns the raw gateway response as a dict."""
    payload = {
        "amount": to_paise(amount),
        "currency": currency,
        "receipt": receipt[:40],
        "notes": notes or {},
    }
    if not is_live():
        return {
            "id": f"order_sim{secrets.token_hex(8)}",
            "entity": "order",
            "status": "created",
            "simulated": True,
            **payload,
        }
    try:
        return _client().order.create(payload)
    except Exception:
        logger.exception("Razorpay order creation failed.")
        raise


def expected_signature(order_id, payment_id):
    """`HMAC_SHA256(order_id + "|" + payment_id, key_secret)` — Razorpay's scheme."""
    return hmac.new(
        _secret().encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_payment_signature(*, order_id, payment_id, signature):
    if not (order_id and payment_id and signature):
        return False
    return hmac.compare_digest(expected_signature(order_id, payment_id), signature)


def verify_webhook_signature(*, body, signature):
    """Validate the `X-Razorpay-Signature` header against the raw request body."""
    if not signature:
        return False
    if isinstance(body, str):
        body = body.encode()
    digest = hmac.new(_webhook_secret().encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def simulate_payment(order_id):
    """Mint a payment id + valid signature for a simulated checkout."""
    payment_id = f"pay_sim{secrets.token_hex(8)}"
    return payment_id, expected_signature(order_id, payment_id)
