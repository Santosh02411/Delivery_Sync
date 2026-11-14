"""
Running Alembic from the application (and from tests).

    upgrade_to_head(engine)   bring the database to the latest schema
    migration_status(engine)  {"current": ..., "head": ..., "up_to_date": bool}

What startup does (main.py), controlled by environment variables:

  DB_AUTO_MIGRATE=1 (default)  upgrade to the latest revision at startup. Convenient for local development and
                               single-instance deployments. Concurrent instances are safe: on PostgreSQL
                               alembic/env.py holds an advisory lock, so one runs the upgrade and the others wait.
  DB_AUTO_MIGRATE=0            do NOT touch the schema. Run `alembic upgrade head` yourself as a release step
                               (Render: preDeployCommand; Docker: a one-off command). If the database is behind,
                               a loud warning is logged — or the app refuses to start when DB_REQUIRE_HEAD=1.

Before upgrading a SQLite file that already has data, a consistent copy is written next to it
(<db>.pre-<revision>-<timestamp>.bak) so a bad upgrade can be undone by restoring the file. For PostgreSQL the
backup is your platform's job (pg_dump / managed snapshots) — see docs/MIGRATIONS.md.
"""

import logging
import os
import sqlite3
from datetime import datetime

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

logger = logging.getLogger(__name__)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def alembic_config(url: str | None = None) -> Config:
    config = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    if url:
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))  # ConfigParser treats % specially
    return config


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def migration_status(engine) -> dict:
    current, head = current_revision(engine), head_revision()
    return {"current": current, "head": head, "up_to_date": current == head}


def _sqlite_path(engine) -> str | None:
    if engine.dialect.name != "sqlite":
        return None
    path = engine.url.database
    return path if path and path != ":memory:" and os.path.exists(path) else None


def backup_sqlite_before_upgrade(engine, label: str) -> str | None:
    """A consistent copy of a SQLite file that already holds data. Returns its path (or None if not applicable)."""
    path = _sqlite_path(engine)
    if not path or os.path.getsize(path) == 0:
        return None
    target = f"{path}.pre-{label}-{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}.bak"
    source = sqlite3.connect(path)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)   # SQLite's online backup API: consistent even if the file is in use
        finally:
            destination.close()
    finally:
        source.close()
    return target


def upgrade_to_head(engine, backup: bool = True) -> dict:
    """Upgrades `engine`'s database to the latest revision and returns migration_status() afterwards."""
    before = migration_status(engine)
    if before["up_to_date"]:
        logger.info("Database schema is up to date (revision %s).", before["head"])
        return before

    backup_path = None
    if backup:
        try:
            backup_path = backup_sqlite_before_upgrade(engine, before["head"])
        except Exception:  # noqa: BLE001 - a failed backup must be visible, but the choice to continue is the operator's
            logger.exception("Could not back up the SQLite database before upgrading; continuing without a backup.")
    if backup_path:
        logger.info("Backed up the database to %s before upgrading.", backup_path)

    logger.info("Upgrading database schema: %s -> %s", before["current"] or "(none)", before["head"])
    command.upgrade(alembic_config(engine.url.render_as_string(hide_password=False)), "head")

    after = migration_status(engine)
    logger.info("Database schema upgraded to revision %s.", after["current"])
    return after


def apply_startup_policy(engine, environ=os.environ) -> dict | None:
    """What main.py does on startup. Returns the status, or None when the schema was deliberately left alone."""
    if environ.get("DB_AUTO_MIGRATE", "1").strip().lower() in ("0", "false", "no", "off"):
        status = migration_status(engine)
        if not status["up_to_date"]:
            message = (f"The database is at revision {status['current'] or '(none)'} but this release needs {status['head']}. "
                       "Run `alembic upgrade head` (DB_AUTO_MIGRATE=0 disables the automatic upgrade).")
            if environ.get("DB_REQUIRE_HEAD", "0").strip().lower() in ("1", "true", "yes", "on"):
                raise RuntimeError(message)
            logger.warning(message)
        return None
    return upgrade_to_head(engine)
