"""
Helpers for Alembic revisions (alembic/versions/*.py).

WHY EVERY HELPER IS "IF MISSING"
--------------------------------
Before Alembic, the schema was kept up to date by `Base.metadata.create_all()` plus a lightweight "add the
columns a model gained" step that ran on every start. Existing databases therefore sit at all sorts of
in-between points: some already have a column or table a revision is about to add. A revision that blindly
does `ADD COLUMN` would crash on those. So the primitives below check first and do nothing when the change is
already in place — which also makes every revision safe to re-run after a half-finished upgrade.

  apply_schema(...)           the baseline: creates what is missing, catches OLD tables up (nullable columns,
                              exactly what the old lightweight migrator did), never drops or alters anything
  create_table_if_missing()   add_column_if_missing()   create_index_if_missing()   drop_*_if_exists()

`op` (Alembic operations), `sa` (the sqlalchemy module) are passed in rather than imported so the logic is
unit-tested without a database (tests/test_migration_helpers.py); real revisions pass the real ones.
"""

import logging

logger = logging.getLogger("alembic.helpers")


# --------------------------------------------------------------------------------------
# Building SQLAlchemy objects from the frozen schema description
# --------------------------------------------------------------------------------------

def _pg_enum_factory():
    from sqlalchemy.dialects import postgresql
    return postgresql.ENUM


def column_type(sa, col: dict, dialect: str, pg_enum=None):
    kind = col["type"]
    if kind == "Enum":
        values, name = col["enum"]["values"], col["enum"]["name"]
        if dialect == "postgresql":
            # The TYPE is created explicitly (and idempotently) before the table — see ensure_enum_types().
            return (pg_enum or _pg_enum_factory())(*values, name=name, create_type=False)
        return sa.Enum(*values, name=name)
    if kind == "String":
        return sa.String(col["length"]) if col.get("length") else sa.String()
    return getattr(sa, kind)()


def ensure_enum_types(sa, bind, columns, dialect: str) -> None:
    """PostgreSQL has real ENUM types that outlive tables; create each one only if it isn't there already."""
    if dialect != "postgresql":
        return
    for col in columns:
        if col["type"] == "Enum":
            sa.Enum(*col["enum"]["values"], name=col["enum"]["name"]).create(bind, checkfirst=True)


def sql_default(sa, value, dialect: str):
    """A plain scalar as a SQL literal for `DEFAULT ...` (None means: no default)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return sa.text(("true" if value else "false") if dialect == "postgresql" else ("1" if value else "0"))
    if isinstance(value, (int, float)):
        return sa.text(repr(value))
    return sa.text("'" + str(value).replace("'", "''") + "'")


# --------------------------------------------------------------------------------------
# Generic "if missing" primitives (for revisions after the baseline)
# --------------------------------------------------------------------------------------

def _inspector(sa, op):
    return sa.inspect(op.get_bind())


def table_exists(sa, op, table: str) -> bool:
    return table in set(_inspector(sa, op).get_table_names())


def column_exists(sa, op, table: str, column: str) -> bool:
    if not table_exists(sa, op, table):
        return False
    return column in {c["name"] for c in _inspector(sa, op).get_columns(table)}


def index_exists(sa, op, table: str, index: str) -> bool:
    if not table_exists(sa, op, table):
        return False
    return index in {i["name"] for i in _inspector(sa, op).get_indexes(table)}


def create_table_if_missing(sa, op, name: str, *elements, **kwargs) -> bool:
    if table_exists(sa, op, name):
        logger.info("table %s already exists - skipped", name)
        return False
    op.create_table(name, *elements, **kwargs)
    return True


def add_column_if_missing(sa, op, table: str, column) -> bool:
    if column_exists(sa, op, table, column.name):
        logger.info("column %s.%s already exists - skipped", table, column.name)
        return False
    op.add_column(table, column)
    return True


def create_index_if_missing(sa, op, name: str, table: str, columns, unique: bool = False) -> bool:
    if index_exists(sa, op, table, name):
        logger.info("index %s already exists - skipped", name)
        return False
    op.create_index(name, table, list(columns), unique=unique)
    return True


def drop_index_if_exists(sa, op, name: str, table: str) -> bool:
    if not index_exists(sa, op, table, name):
        return False
    op.drop_index(name, table_name=table)
    return True


def drop_table_if_exists(sa, op, name: str) -> bool:
    if not table_exists(sa, op, name):
        return False
    op.drop_table(name)
    return True


# --------------------------------------------------------------------------------------
# The baseline
# --------------------------------------------------------------------------------------

def apply_schema(sa, op, tables, pg_enum=None) -> dict:
    """
    Brings the database to the described schema WITHOUT ever dropping or altering anything that exists:

      * a table that is missing is created (columns, unique constraints, indexes);
      * a table that exists but lacks columns is caught up by ADDING them, always NULLABLE (a NOT NULL column
        can't be added to a table that already has rows) and with the plain scalar DEFAULT, if any, so existing
        rows get a sensible value — identical to what the old startup migrator did;
      * indexes that are missing are created; a missing UNIQUE index is skipped with a warning instead
        (building it could fail on existing duplicate rows, which needs a human decision).

    Returns {"created_tables", "added_columns", "created_indexes", "skipped_unique_indexes"} for the log.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    report = {"created_tables": [], "added_columns": [], "created_indexes": [], "skipped_unique_indexes": []}

    for table in tables:
        name = table["name"]
        if name not in existing_tables:
            ensure_enum_types(sa, bind, table["columns"], dialect)
            elements = [
                sa.Column(c["name"], column_type(sa, c, dialect, pg_enum), primary_key=c["primary_key"], nullable=c["nullable"])
                for c in table["columns"]
            ]
            # `unique=True` without an index is a plain UNIQUE constraint; with an index it is a unique INDEX (below).
            elements += [sa.UniqueConstraint(c["name"]) for c in table["columns"] if c["unique"] and not c["index"]]
            elements += [sa.UniqueConstraint(*u["columns"], name=u["name"]) for u in table["unique_constraints"]]
            op.create_table(name, *elements)
            report["created_tables"].append(name)
            existing_indexes = set()
        else:
            existing_cols = {c["name"] for c in inspector.get_columns(name)}
            for c in table["columns"]:
                if c["name"] in existing_cols:
                    continue
                ensure_enum_types(sa, bind, [c], dialect)
                op.add_column(name, sa.Column(
                    c["name"], column_type(sa, c, dialect, pg_enum), nullable=True,
                    server_default=sql_default(sa, c.get("default"), dialect),
                ))
                report["added_columns"].append(f"{name}.{c['name']}")
            existing_indexes = {i["name"] for i in inspector.get_indexes(name)}

        for index in table["indexes"]:
            if index["name"] in existing_indexes:
                continue
            if name in existing_tables and index["unique"]:
                logger.warning("Unique index %s on existing table %s was NOT created automatically "
                               "(existing rows may contain duplicates). Create it by hand once the data is clean.", index["name"], name)
                report["skipped_unique_indexes"].append(index["name"])
                continue
            op.create_index(index["name"], name, index["columns"], unique=index["unique"])
            report["created_indexes"].append(index["name"])

    for key, items in report.items():
        if items:
            logger.info("baseline: %s: %d", key, len(items))
    return report
