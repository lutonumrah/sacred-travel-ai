"""Run the periodic jobs once, or forever with --loop (the `scheduler` container).

    python manage.py run_scheduled_jobs                       # once, prints a summary
    python manage.py run_scheduled_jobs --loop --interval 60  # every minute until SIGTERM
"""

import signal
import threading
import time
from pathlib import Path

from django.core.management.base import BaseCommand

from core import scheduler

# Touched after every loop; the compose healthcheck reads its age.
HEARTBEAT = Path("/tmp/scheduler-heartbeat")


class Command(BaseCommand):
    help = "Run follow-up reminders, chat auto-resume and the nightly backup."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Keep running until stopped.")
        parser.add_argument(
            "--interval", type=int, default=60, help="Seconds between runs with --loop."
        )

    def handle(self, *args, **options):
        if not options["loop"]:
            results = scheduler.run_jobs()
            self.stdout.write(scheduler.summarise(results))
            return

        interval = max(options["interval"], 5)
        stop = threading.Event()

        def request_stop(signum, _frame):
            self.stdout.write(f"Received signal {signum}; stopping after the current run.")
            stop.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        self.stdout.write(f"Scheduler started: running jobs every {interval}s.")

        while not stop.is_set():
            started = time.monotonic()
            results = scheduler.run_jobs()
            # Quiet minutes stay out of the log; anything that acted or failed is shown.
            if scheduler.did_something(results):
                self.stdout.write(scheduler.summarise(results))
            self._heartbeat()
            stop.wait(max(interval - (time.monotonic() - started), 1))
        self.stdout.write("Scheduler stopped.")

    def _heartbeat(self):
        try:
            HEARTBEAT.touch()
        except OSError:
            pass
