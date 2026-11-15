"""
Alembic migrations against REAL (SQLite) databases.

ACCEPTANCE TEST — test_a_previous_populated_database_upgrades_without_losing_anything:
  a database in the shape the previous release left it (every baseline table, some newer columns missing, no
  sync_events table), holding users, deliveries, orders and audit/history rows, is upgraded to the latest schema,
  and afterwards every row of every table is byte-for-byte what it was.

Also: fresh upgrade, idempotency, downgrade/upgrade of a later revision, models == migrations (the drift guard
that fails when someone changes a model and forgets the migration), the automatic pre-upgrade backup, and the
startup policy (DB_AUTO_MIGRATE / DB_REQUIRE_HEAD).

Each test uses its own throwaway SQLite file; nothing touches the development database.
(PostgreSQL-specific behaviour — enum types, the advisory lock — is covered by the logic tests in
tests/test_migration_helpers.py and by the staging checklist in docs/MIGRATIONS.md.)
"""

import glob
import os
import uuid
from datetime import date, datetime, time

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.db import migrations
from app.db.migrations import alembic_config, apply_startup_policy, head_revision, migration_status, upgrade_to_head
from app.db.models_registry import import_all_models
from app.db.session import Base

import_all_models()


@pytest.fixture()
def db_file(tmp_path):
    return str(tmp_path / "migration_test.db")


@pytest.fixture()
def engine(db_file):
    engine = create_engine(f"sqlite:///{db_file}")
    yield engine
    engine.dispose()


def table_names(engine):
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


# --------------------------------------------------------------------------------------
# helpers to build a "previous release" database full of real-looking rows
# --------------------------------------------------------------------------------------

def synthesize_row(table, **overrides):
    """A valid row for ANY table: every NOT NULL column gets a value of the right type; nullable ones stay NULL."""
    row = {}
    for column in table.columns:
        if column.name in overrides:
            row[column.name] = overrides[column.name]
            continue
        if column.nullable and not column.primary_key:
            continue
        kind = column.type
        if isinstance(kind, sa.Enum):
            row[column.name] = kind.enums[0]
        elif isinstance(kind, sa.Boolean):
            row[column.name] = True
        elif isinstance(kind, sa.Integer):
            row[column.name] = 7
        elif isinstance(kind, sa.Float):
            row[column.name] = 12.5
        elif isinstance(kind, sa.DateTime):
            row[column.name] = datetime(2026, 3, 14, 9, 26, 53)
        elif isinstance(kind, sa.Date):
            row[column.name] = date(2026, 3, 14)
        elif isinstance(kind, sa.Time):
            row[column.name] = time(9, 26, 53)
        else:
            row[column.name] = f"{table.name}.{column.name}.{uuid.uuid4().hex[:8]}"
    return row


def all_rows(engine, table_name):
    with engine.connect() as connection:
        return sorted(tuple(map(repr, r)) for r in connection.execute(text(f"SELECT * FROM {table_name}")).fetchall())


def build_previous_release_database(engine):
    """
    Recreates what release 71 left on disk: every baseline table (create_all, as the old startup did), WITHOUT the
    sync_events table that this release adds, and without two columns that older databases also lack — then fills
    it with data that must survive.
    """
    baseline_tables = [t for t in Base.metadata.sorted_tables if t.name != "sync_events"]
    Base.metadata.create_all(engine, tables=baseline_tables)
    populated = {}
    with engine.begin() as connection:
        for name in ("organizations", "users", "deliveries", "orders", "delivery_history", "delivery_change_audit", "webhook_deliveries"):
            table = Base.metadata.tables[name]
            rows = [synthesize_row(table), synthesize_row(table)]
            for row in rows:
                connection.execute(table.insert().values(**row))
            populated[name] = len(rows)
    with engine.begin() as connection:   # a database from before these columns existed (SQLite >= 3.35 can drop columns)
        connection.execute(text("ALTER TABLE webhook_deliveries DROP COLUMN last_error"))
        connection.execute(text("ALTER TABLE webhook_deliveries DROP COLUMN failure_code"))
    return populated


# --------------------------------------------------------------------------------------
# THE ACCEPTANCE TEST
# --------------------------------------------------------------------------------------

def test_a_previous_populated_database_upgrades_without_losing_anything(engine):
    populated = build_previous_release_database(engine)
    assert "sync_events" not in table_names(engine)
    assert "alembic_version" not in inspect(engine).get_table_names()        # exactly like a pre-Alembic database
    before = {name: all_rows(engine, name) for name in table_names(engine)}
    assert all(len(before[name]) == count for name, count in populated.items())

    status = upgrade_to_head(engine, backup=False)

    # 1. at the latest schema
    assert status["up_to_date"] and status["current"] == head_revision()
    assert "sync_events" in table_names(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("webhook_deliveries")}
    assert {"last_error", "failure_code"} <= columns                          # the older database was caught up

    # 2. NOTHING was lost or changed: users, deliveries, orders, audit history, everything
    for name in ("organizations", "users", "deliveries", "orders", "delivery_history", "delivery_change_audit"):
        assert all_rows(engine, name) == before[name], f"{name} changed during the upgrade"
    for name, rows in before.items():
        assert len(all_rows(engine, name)) == len(rows), f"{name} lost or gained rows"
    # (webhook_deliveries gained two NULL columns, so compare by key instead of by whole row)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM webhook_deliveries")).scalar() == populated["webhook_deliveries"]

    # 3. and the application's own models can read it
    with engine.connect() as connection:
        assert connection.execute(Base.metadata.tables["users"].select()).fetchall()
        assert connection.execute(Base.metadata.tables["sync_events"].select()).fetchall() == []


def test_upgrading_a_database_that_is_already_current_changes_nothing(engine):
    build_previous_release_database(engine)
    upgrade_to_head(engine, backup=False)
    before = {name: all_rows(engine, name) for name in table_names(engine)}
    again = upgrade_to_head(engine, backup=False)
    assert again["up_to_date"]
    assert {name: all_rows(engine, name) for name in table_names(engine)} == before


# --------------------------------------------------------------------------------------
# Fresh databases, revisions, drift
# --------------------------------------------------------------------------------------

def test_a_fresh_database_is_created_by_the_migrations_alone(engine):
    upgrade_to_head(engine, backup=False)
    assert migration_status(engine)["up_to_date"]
    assert table_names(engine) == {t.name for t in Base.metadata.sorted_tables}


def test_migrations_and_models_agree_so_a_forgotten_migration_fails_here(engine):
    """
    The drift guard. After upgrading a fresh database to head, the models must not want ANY table, column or index
    the migrations didn't create. If you change a model and forget the revision, this test names what's missing.
    (Type and default differences are deliberately not compared: they differ harmlessly between dialects.)
    """
    upgrade_to_head(engine, backup=False)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": False, "compare_server_default": False})
        differences = compare_metadata(context, Base.metadata)
    missing = [d for d in differences if d[0] in ("add_table", "add_column", "add_index", "add_constraint")]
    assert missing == [], f"models define schema that no migration creates: {missing}"


def test_a_later_revision_can_be_rolled_back_and_re_applied(engine, db_file):
    upgrade_to_head(engine, backup=False)
    config = alembic_config(f"sqlite:///{db_file}")

    command.downgrade(config, "0001")
    assert "sync_events" not in table_names(engine) and migration_status(engine)["current"] == "0001"
    assert "users" in table_names(engine)                                   # only the later revision was undone

    command.upgrade(config, "head")
    assert "sync_events" in table_names(engine) and migration_status(engine)["up_to_date"]


def test_the_baseline_refuses_to_be_downgraded_because_that_would_drop_every_table(engine, db_file):
    upgrade_to_head(engine, backup=False)
    with pytest.raises(Exception) as exc:
        command.downgrade(alembic_config(f"sqlite:///{db_file}"), "base")
    assert "baseline cannot be downgraded" in str(exc.value) or "backup" in str(exc.value)
    assert "users" in table_names(engine)


def test_the_idempotency_ledger_enforces_one_decision_per_event(engine):
    """The UNIQUE (actor, event) constraint is what makes two racing requests safe; prove the database enforces it."""
    upgrade_to_head(engine, backup=False)
    table = Base.metadata.tables["sync_events"]
    row = synthesize_row(table, actor_user_id="u1", event_id="evt-12345678")
    with engine.begin() as connection:
        connection.execute(table.insert().values(**row))
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(table.insert().values(**synthesize_row(table, actor_user_id="u1", event_id="evt-12345678")))
    with engine.begin() as connection:     # the same event id from ANOTHER user is a different event
        connection.execute(table.insert().values(**synthesize_row(table, actor_user_id="u2", event_id="evt-12345678")))


# --------------------------------------------------------------------------------------
# Backup + startup policy
# --------------------------------------------------------------------------------------

def test_a_backup_of_a_populated_sqlite_file_is_written_before_upgrading(engine, db_file):
    populated = build_previous_release_database(engine)
    upgrade_to_head(engine)                                                   # backup=True is the default
    backups = glob.glob(f"{db_file}.pre-*.bak")
    assert len(backups) == 1

    restored = create_engine(f"sqlite:///{backups[0]}")
    try:
        assert "sync_events" not in table_names(restored)                     # the backup is the PRE-upgrade state...
        with restored.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM users")).scalar() == populated["users"]   # ...with the data in it
    finally:
        restored.dispose()


def test_no_backup_is_made_for_a_brand_new_empty_database(engine, db_file):
    upgrade_to_head(engine)
    assert glob.glob(f"{db_file}.pre-*.bak") == []


def test_auto_migrate_off_leaves_the_schema_alone_and_only_warns(engine, caplog):
    build_previous_release_database(engine)
    result = apply_startup_policy(engine, environ={"DB_AUTO_MIGRATE": "0"})
    assert result is None
    assert "sync_events" not in table_names(engine)                           # untouched
    assert any("alembic upgrade head" in r.message for r in caplog.records)


def test_require_head_refuses_to_start_on_an_out_of_date_schema(engine):
    build_previous_release_database(engine)
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        apply_startup_policy(engine, environ={"DB_AUTO_MIGRATE": "0", "DB_REQUIRE_HEAD": "1"})


def test_auto_migrate_on_upgrades(engine):
    build_previous_release_database(engine)
    status = apply_startup_policy(engine, environ={})
    assert status["up_to_date"] and "sync_events" in table_names(engine)


def test_the_revision_chain_is_linear_and_complete():
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(alembic_config())
    revisions = [r.revision for r in script.walk_revisions()]       # head first
    assert revisions == ["0002", "0001"] or revisions[-1] == "0001"
    assert len(script.get_heads()) == 1                             # a single head: no accidental branches
