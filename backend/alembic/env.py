"""
Alembic environment.

 * The URL comes from the application (DATABASE_URL / app/db/session.py), unless a connection is handed in
   programmatically (app/db/migrations.py does that when the app upgrades itself at startup).
 * On PostgreSQL a session-level ADVISORY LOCK is held for the whole upgrade, so several app instances
   starting at the same moment (a rolling deploy, autoscaling) can't run migrations concurrently: one does the
   work, the others wait and then find nothing left to do.
 * render_as_batch makes ALTERs work on SQLite (which can't alter columns in place); it is a no-op elsewhere.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.db.models_registry import import_all_models  # noqa: E402
from app.db.session import Base, SQLALCHEMY_DATABASE_URL  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

import_all_models()
target_metadata = Base.metadata

MIGRATION_LOCK_KEY = 727_000_001  # arbitrary constant shared by every instance of this app


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or SQLALCHEMY_DATABASE_URL


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name == "alembic_version")


def _configure(connection=None, url=None):
    context.configure(
        connection=connection, url=url, target_metadata=target_metadata, literal_binds=connection is None,
        compare_type=True, render_as_batch=True, include_object=include_object,
        transaction_per_migration=True,   # a failure in revision N never undoes revisions already applied
    )


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: emit the SQL instead of running it (to review it, or hand it to a DBA)."""
    _configure(url=_url())
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection) -> None:
    postgres = connection.dialect.name == "postgresql"
    if postgres:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        connection.commit()
    try:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    finally:
        if postgres:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
            connection.commit()


def run_migrations_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        _run_with_connection(supplied)
        return
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _url()
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        _run_with_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
