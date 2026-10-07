"""Nightly SQLite snapshots.

`sqlite3.Connection.backup` copies the live database page by page while the
web workers keep reading and writing, so the site never stops for it. Files
land beside the database as `backups/db-YYYY-MM-DD.sqlite3`; a `.last-backup`
marker holds the local date of the last good run, so a restarted scheduler
does not take a second copy the same day.
"""

import logging
import re
import sqlite3
from pathlib import Path

from django.conf import settings
from django.db import connections
from django.utils import timezone

logger = logging.getLogger(__name__)

MARKER = ".last-backup"
# Only dated snapshots rotate; a hand-made copy with another name is left alone.
SNAPSHOT_NAME = re.compile(r"^db-\d{4}-\d{2}-\d{2}\.sqlite3$")


def database_path(alias="default"):
    """Path of the SQLite database file, or None for any other engine."""
    config = connections.databases[alias]
    if "sqlite3" not in config["ENGINE"]:
        return None
    name = str(config["NAME"])
    if name == ":memory:" or name.startswith("file:"):
        return None
    return Path(name)


def backup_dir(db_path=None):
    configured = getattr(settings, "BACKUP_DIR", "")
    if configured:
        return Path(configured)
    db_path = db_path or database_path()
    return (db_path.parent if db_path else Path(settings.BASE_DIR)) / "backups"


def backup_database(*, source, destination):
    """Online copy of `source` into `destination`, written to a temp file first."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    partial.unlink(missing_ok=True)
    src = sqlite3.connect(str(source), timeout=30)
    try:
        dst = sqlite3.connect(str(partial))
        try:
            # Small steps keep each read short so writers are never blocked for long.
            src.backup(dst, pages=1024)
        finally:
            dst.close()
    finally:
        src.close()
    partial.replace(destination)
    return destination


def rotate(directory, keep):
    """Delete all but the newest `keep` snapshots. Returns the removed paths."""
    snapshots = sorted(
        (path for path in directory.iterdir() if SNAPSHOT_NAME.match(path.name)),
        key=lambda path: path.name,
        reverse=True,
    )
    removed = []
    for path in snapshots[max(keep, 1):]:
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed


def _last_run(directory):
    try:
        return (directory / MARKER).read_text().strip()
    except OSError:
        return ""


def run_nightly_backup(*, now=None, force=False, source=None):
    """Take today's snapshot if it is due. Returns a short status string."""
    if not getattr(settings, "BACKUP_ENABLED", True):
        return "disabled"
    source = source or database_path()
    if source is None:
        logger.info("Database is not SQLite; nightly backup skipped.")
        return "skipped: not sqlite"
    if not source.exists():
        return "skipped: no database file"

    local = timezone.localtime(now or timezone.now())
    today = local.date().isoformat()
    directory = backup_dir(source)
    if not force:
        if local.hour < settings.BACKUP_HOUR:
            return "not due"
        if _last_run(directory) == today:
            return "done today"

    destination = backup_database(source=source, destination=directory / f"db-{today}.sqlite3")
    (directory / MARKER).write_text(today + "\n")
    removed = rotate(directory, settings.BACKUP_KEEP_DAYS)
    size_kb = destination.stat().st_size // 1024
    logger.info(
        "Backed up %s to %s (%d KB); removed %d old snapshot(s).",
        source,
        destination,
        size_kb,
        len(removed),
    )
    return f"saved {destination.name}"
