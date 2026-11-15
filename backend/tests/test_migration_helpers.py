"""
Unit tests for app/db/migration_helpers.py and the frozen baseline (app/db/baseline_schema.py), using recording
fakes for Alembic's `op` and SQLAlchemy's `sa` — no database involved, so they run anywhere and fast.

(tests/test_migrations.py runs the same revisions against REAL databases; this file pins down the LOGIC:
what gets created on a fresh database, what a legacy database is caught up with, and what is never touched.)
"""

from app.db import migration_helpers as helpers
from app.db.baseline_schema import BASELINE_TABLES


# --------------------------------------------------------------------------------------
# Recording fakes
# --------------------------------------------------------------------------------------

class _Type:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs

    def __repr__(self):
        return f"{type(self).__name__}{self.args}"


class FakeEnum(_Type):
    created = []

    def create(self, bind, checkfirst=False):
        FakeEnum.created.append((self.kwargs["name"], checkfirst))


class FakeColumn:
    def __init__(self, name, type_, **kwargs):
        self.name, self.type, self.kwargs = name, type_, kwargs


class FakeUnique:
    def __init__(self, *columns, name=None):
        self.columns, self.name = columns, name


class FakeInspector:
    def __init__(self, existing):
        self.existing = existing   # {table: {"columns": [names], "indexes": [names]}}

    def get_table_names(self):
        return list(self.existing)

    def get_columns(self, table):
        return [{"name": n} for n in self.existing[table]["columns"]]

    def get_indexes(self, table):
        return [{"name": n} for n in self.existing[table]["indexes"]]


class FakeBind:
    def __init__(self, dialect):
        self.dialect = type("D", (), {"name": dialect})()


def make_sa(existing):
    sa = type("sa", (), {})()
    for kind in ("String", "Integer", "DateTime", "Float", "Boolean", "Date", "Time", "Text"):
        setattr(sa, kind, type(kind, (_Type,), {}))
    sa.Enum, sa.Column, sa.UniqueConstraint = FakeEnum, FakeColumn, FakeUnique
    sa.text = lambda s: ("TEXT", s)
    sa.inspect = lambda bind: FakeInspector(existing)
    return sa


class FakeOp:
    def __init__(self, dialect="sqlite"):
        self.bind = FakeBind(dialect)
        self.created_tables, self.added_columns, self.created_indexes, self.dropped = {}, [], [], []

    def get_bind(self):
        return self.bind

    def create_table(self, name, *elements, **kw):
        self.created_tables[name] = elements

    def add_column(self, table, column):
        self.added_columns.append((table, column))

    def create_index(self, name, table, columns, unique=False):
        self.created_indexes.append((name, table, list(columns), unique))

    def drop_table(self, name):
        self.dropped.append(("table", name))

    def drop_index(self, name, table_name=None):
        self.dropped.append(("index", name))


def full_existing_schema():
    """What a database created by the OLD create_all() looks like: every baseline table, column and index."""
    return {t["name"]: {"columns": [c["name"] for c in t["columns"]], "indexes": [i["name"] for i in t["indexes"]]} for t in BASELINE_TABLES}


# --------------------------------------------------------------------------------------
# The frozen baseline itself
# --------------------------------------------------------------------------------------

def test_the_baseline_describes_the_whole_schema_it_was_frozen_from():
    assert len(BASELINE_TABLES) == 71
    assert sum(len(t["columns"]) for t in BASELINE_TABLES) == 722
    assert sum(len(t["indexes"]) for t in BASELINE_TABLES) == 166
    assert sum(1 for t in BASELINE_TABLES for c in t["columns"] if c["type"] == "Enum") == 13


def test_every_baseline_table_is_well_formed():
    names = [t["name"] for t in BASELINE_TABLES]
    assert len(names) == len(set(names)) and "sync_events" not in names       # sync_events belongs to revision 0002
    index_names = [i["name"] for t in BASELINE_TABLES for i in t["indexes"]]
    assert len(index_names) == len(set(index_names))                           # index names are global in PostgreSQL
    allowed = {"String", "Integer", "DateTime", "Float", "Boolean", "Date", "Time", "Text", "Enum"}
    for table in BASELINE_TABLES:
        columns = [c["name"] for c in table["columns"]]
        assert len(columns) == len(set(columns)), table["name"]
        assert any(c["primary_key"] for c in table["columns"]), f"{table['name']} has no primary key"
        for column in table["columns"]:
            assert column["type"] in allowed, (table["name"], column)
            if column["type"] == "Enum":
                assert column["enum"]["name"] and len(column["enum"]["values"]) >= 2
            if column["primary_key"]:
                assert column["nullable"] is False
        for index in table["indexes"]:
            assert set(index["columns"]) <= set(columns)
        for unique in table["unique_constraints"]:
            assert set(unique["columns"]) <= set(columns)


def test_known_tables_carry_the_columns_the_rest_of_the_code_relies_on():
    by_name = {t["name"]: {c["name"] for c in t["columns"]} for t in BASELINE_TABLES}
    assert {"id", "username", "email", "role", "org_id"} <= by_name["users"]
    assert {"id", "version", "status", "agent_id", "attempt_count"} <= by_name["deliveries"]
    assert {"last_error", "failure_code"} <= by_name["webhook_deliveries"]        # added by the SSRF work
    assert {"delivery_id", "outcome", "version_before", "version_after"} <= by_name["delivery_change_audit"]


# --------------------------------------------------------------------------------------
# Fresh database
# --------------------------------------------------------------------------------------

def test_a_fresh_database_gets_every_table_and_index():
    op, sa = FakeOp(), make_sa({})
    report = helpers.apply_schema(sa, op, BASELINE_TABLES)

    assert len(op.created_tables) == 71 and report["created_tables"] == [t["name"] for t in BASELINE_TABLES]
    assert len(op.created_indexes) == 166
    assert op.added_columns == [] and op.dropped == []
    users = op.created_tables["users"]
    column_names = [e.name for e in users if isinstance(e, FakeColumn)]
    assert column_names[:3] == ["id", "username", "email"]
    assert next(e for e in users if isinstance(e, FakeColumn) and e.name == "id").kwargs == {"primary_key": True, "nullable": False}


def test_unique_columns_become_unique_indexes_and_unique_constraints_are_kept():
    op = FakeOp()
    helpers.apply_schema(make_sa({}), op, BASELINE_TABLES)
    assert ("ix_users_username", "users", ["username"], True) in op.created_indexes          # unique=True + index=True
    coupons = [e for e in op.created_tables["coupons"] if isinstance(e, FakeUnique) and e.name]
    assert [(u.name, u.columns) for u in coupons] == [("uq_coupon_code_per_org", ("org_id", "code"))]


def test_enum_columns_are_created_with_their_member_names():
    op = FakeOp()
    helpers.apply_schema(make_sa({}), op, BASELINE_TABLES)
    role = next(e for e in op.created_tables["users"] if isinstance(e, FakeColumn) and e.name == "role")
    assert isinstance(role.type, FakeEnum) and role.type.args == ("agent", "dispatcher", "admin") and role.type.kwargs == {"name": "userrole"}


def test_postgresql_enum_types_are_created_once_and_explicitly_before_their_table():
    FakeEnum.created.clear()
    op = FakeOp(dialect="postgresql")

    class PgEnum(_Type):
        pass

    helpers.apply_schema(make_sa({}), op, BASELINE_TABLES, pg_enum=PgEnum)

    assert len(FakeEnum.created) == 13 and all(checkfirst for _, checkfirst in FakeEnum.created)   # CREATE TYPE ... only if absent
    role = next(e for e in op.created_tables["users"] if isinstance(e, FakeColumn) and e.name == "role")
    assert isinstance(role.type, PgEnum) and role.type.kwargs == {"name": "userrole", "create_type": False}   # the table doesn't re-create it


# --------------------------------------------------------------------------------------
# Existing (legacy) database
# --------------------------------------------------------------------------------------

def test_an_up_to_date_legacy_database_is_left_completely_alone():
    op, sa = FakeOp(), make_sa(full_existing_schema())
    report = helpers.apply_schema(sa, op, BASELINE_TABLES)
    assert (op.created_tables, op.added_columns, op.created_indexes, op.dropped) == ({}, [], [], [])
    assert report == {"created_tables": [], "added_columns": [], "created_indexes": [], "skipped_unique_indexes": []}


def test_a_legacy_database_missing_columns_is_caught_up_with_nullable_columns_and_never_loses_anything():
    existing = full_existing_schema()
    existing["webhook_deliveries"]["columns"].remove("last_error")
    existing["webhook_deliveries"]["columns"].remove("failure_code")
    op = FakeOp()
    report = helpers.apply_schema(make_sa(existing), op, BASELINE_TABLES)

    assert report["added_columns"] == ["webhook_deliveries.last_error", "webhook_deliveries.failure_code"]
    for table, column in op.added_columns:
        assert table == "webhook_deliveries" and column.kwargs["nullable"] is True          # never NOT NULL on a table with rows
    assert op.created_tables == {} and op.dropped == []


def test_catch_up_columns_get_the_plain_default_when_the_model_has_one():
    existing = full_existing_schema()
    existing["agent_settlements"]["columns"].remove("total_expected")      # model default 0.0
    existing["agent_settlements"]["columns"].remove("status")              # model default 'open'
    op = FakeOp()
    helpers.apply_schema(make_sa(existing), op, BASELINE_TABLES)
    defaults = {c.name: c.kwargs["server_default"] for _, c in op.added_columns}
    assert defaults == {"total_expected": ("TEXT", "0.0"), "status": ("TEXT", "'open'")}


def test_boolean_and_string_defaults_are_rendered_correctly_per_dialect():
    sa = make_sa({})
    assert helpers.sql_default(sa, True, "postgresql") == ("TEXT", "true")
    assert helpers.sql_default(sa, False, "sqlite") == ("TEXT", "0")
    assert helpers.sql_default(sa, "it's", "sqlite") == ("TEXT", "'it''s'")        # quotes escaped
    assert helpers.sql_default(sa, 3, "sqlite") == ("TEXT", "3")
    assert helpers.sql_default(sa, None, "sqlite") is None


def test_a_missing_table_in_a_legacy_database_is_created_and_the_rest_untouched():
    existing = full_existing_schema()
    del existing["coupons"]
    op = FakeOp()
    report = helpers.apply_schema(make_sa(existing), op, BASELINE_TABLES)
    assert report["created_tables"] == ["coupons"] and list(op.created_tables) == ["coupons"]
    assert all(i[1] == "coupons" for i in op.created_indexes)


def test_missing_indexes_are_created_but_a_missing_UNIQUE_index_is_skipped_not_forced():
    """Building a unique index over existing rows can fail on duplicates; that needs a human, not a startup crash."""
    existing = full_existing_schema()
    existing["users"]["indexes"].remove("ix_users_username")        # unique
    existing["users"]["indexes"].remove("ix_users_id")              # plain
    op = FakeOp()
    report = helpers.apply_schema(make_sa(existing), op, BASELINE_TABLES)
    assert report["created_indexes"] == ["ix_users_id"]
    assert report["skipped_unique_indexes"] == ["ix_users_username"]


def test_running_it_twice_is_a_no_op_the_second_time():
    """After the first run the database matches; a re-run (e.g. after a crash mid-upgrade) changes nothing."""
    op = FakeOp()
    helpers.apply_schema(make_sa({}), op, BASELINE_TABLES)
    after_first = {t["name"]: {"columns": [c["name"] for c in t["columns"]], "indexes": [i["name"] for i in t["indexes"]]} for t in BASELINE_TABLES}
    second = FakeOp()
    report = helpers.apply_schema(make_sa(after_first), second, BASELINE_TABLES)
    assert (second.created_tables, second.added_columns, second.created_indexes) == ({}, [], [])
    assert not any(report.values())


# --------------------------------------------------------------------------------------
# The "if missing" primitives used by later revisions
# --------------------------------------------------------------------------------------

def test_primitives_do_nothing_when_the_change_is_already_in_place():
    existing = {"things": {"columns": ["id", "name"], "indexes": ["ix_things_name"]}}
    sa, op = make_sa(existing), FakeOp()

    assert helpers.create_table_if_missing(sa, op, "things", FakeColumn("id", None)) is False
    assert helpers.add_column_if_missing(sa, op, "things", FakeColumn("name", None)) is False
    assert helpers.create_index_if_missing(sa, op, "ix_things_name", "things", ["name"]) is False
    assert (op.created_tables, op.added_columns, op.created_indexes) == ({}, [], [])


def test_primitives_make_the_change_when_it_is_missing():
    existing = {"things": {"columns": ["id"], "indexes": []}}
    sa, op = make_sa(existing), FakeOp()

    assert helpers.create_table_if_missing(sa, op, "others", FakeColumn("id", None)) is True
    assert helpers.add_column_if_missing(sa, op, "things", FakeColumn("name", None)) is True
    assert helpers.create_index_if_missing(sa, op, "ix_things_name", "things", ["name"]) is True
    assert "others" in op.created_tables and op.added_columns[0][0] == "things" and op.created_indexes[0][0] == "ix_things_name"


def test_drop_primitives_only_act_on_what_exists():
    existing = {"things": {"columns": ["id"], "indexes": ["ix_a"]}}
    sa, op = make_sa(existing), FakeOp()
    assert helpers.drop_index_if_exists(sa, op, "ix_a", "things") is True
    assert helpers.drop_index_if_exists(sa, op, "ix_missing", "things") is False
    assert helpers.drop_table_if_exists(sa, op, "things") is True
    assert helpers.drop_table_if_exists(sa, op, "ghost") is False
    assert op.dropped == [("index", "ix_a"), ("table", "things")]
    assert helpers.column_exists(sa, op, "ghost", "x") is False and helpers.index_exists(sa, op, "ghost", "x") is False
