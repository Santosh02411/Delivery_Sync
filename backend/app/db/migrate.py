"""
Poor-man's schema migration for SQLite.

This project doesn't use Alembic (deliberately — zero extra setup, in
keeping with the zero-budget/zero-config philosophy elsewhere in this
project). But that means `Base.metadata.create_all(bind=engine)` in
main.py only creates tables that don't exist yet — it silently does
NOT add new columns to a table that already exists from a previous run.
That's exactly the failure mode of "table orders has no column named
payment_method": the feature added a column to the OrderDB model, but
an existing local dev.db file (with real signups/orders already in it)
was created back when that model had fewer columns, and create_all()
has no way to know it needs to catch that table up.

This walks every model's expected columns against what's actually in
the database on every startup and ALTERs in whatever is missing, so an
existing local .db file stays usable across every future feature added
to this project — never needs to be deleted and started over just
because a model gained a field.
"""

import logging

from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)


def _sql_default_literal(column):
    """
    A SQL-literal DEFAULT clause value for this column, if it has a
    plain scalar default (e.g. default=False, default="online") — None
    if it doesn't, or if the default is a Python callable (e.g.
    default=lambda: str(uuid.uuid4())), which can't be expressed as a
    static SQL literal and isn't needed here anyway (existing rows just
    get NULL for those; every future INSERT sets a real value).
    """
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def run_lightweight_migrations(engine, base):
    """
    Call once at startup, AFTER Base.metadata.create_all(bind=engine).
    By that point every table already exists (create_all made sure of
    that); this only ever needs to ADD COLUMNs to tables that existed
    before this run but are missing fields a model has since gained. For
    a table that create_all() just created fresh, every column already
    matches the model exactly, so this is a harmless no-op for it.

    New columns are always added nullable, regardless of the model's own
    nullable=False — specifically so this can never fail against a
    table that already has rows (SQLite requires a DEFAULT for a NOT
    NULL column added to a non-empty table, and not every column has one
    to give it). Existing rows get NULL/the default for the new column;
    every code path going forward always sets a real value on INSERT
    regardless, so this loosening is invisible in practice.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                default_literal = _sql_default_literal(column)
                stmt = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}"
                if default_literal is not None:
                    stmt += f" DEFAULT {default_literal}"
                logger.info("Lightweight migration: %s", stmt)
                conn.execute(text(stmt))
