"""Periodic jobs run by `manage.py run_scheduled_jobs`.

Each job is independent: one raising is logged and reported, and the rest
still run. Jobs keep their own transactions short because the scheduler is a
third SQLite writer next to the two gunicorn workers.

Not scheduled: payment links and Razorpay orders. A link's expiry is checked
whenever it is opened, and nothing in the business rules closes an unpaid
order on a timer, so there is nothing to sweep.
"""

import logging
import time

from django.db import close_old_connections

logger = logging.getLogger(__name__)


def follow_up_reminders(now):
    from crm.services import send_due_reminders

    return send_due_reminders(now=now)


def auto_resume_chats(now):
    from conversations.services import auto_resume_stale_conversations

    return auto_resume_stale_conversations(now=now)


def nightly_backup(now):
    from .backups import run_nightly_backup

    return run_nightly_backup(now=now)


# (name, callable(now) -> count or short status). Order is the run order.
JOBS = [
    ("reminders", follow_up_reminders),
    ("auto_resume", auto_resume_chats),
    ("backup", nightly_backup),
]


def run_jobs(*, now=None, jobs=None):
    """Run every job once. Returns [(name, ok, result_or_error, seconds)]."""
    from django.utils import timezone

    results = []
    for name, job in jobs or JOBS:
        started = time.monotonic()
        # A long-lived process must not reuse a connection the server dropped.
        close_old_connections()
        try:
            result = job(now or timezone.now())
            ok = True
        except Exception as exc:
            logger.exception("Scheduled job %s failed.", name)
            result, ok = f"{type(exc).__name__}: {exc}", False
        results.append((name, ok, result, time.monotonic() - started))
    close_old_connections()
    return results


def summarise(results):
    parts = []
    for name, ok, result, _seconds in results:
        parts.append(f"{name}={result}" if ok else f"{name}=FAILED({result})")
    total = sum(seconds for *_rest, seconds in results)
    return f"scheduled jobs: {' '.join(parts)} [{total:.2f}s]"


def did_something(results):
    """True if any job failed or acted (a non-zero count or a "saved" status)."""
    for _name, ok, result, _seconds in results:
        if not ok:
            return True
        if isinstance(result, int) and result:
            return True
        if isinstance(result, str) and result.startswith("saved"):
            return True
    return False
