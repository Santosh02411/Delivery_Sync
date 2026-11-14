"""
One-off tool used to FREEZE the Alembic baseline (app/db/baseline_schema.py).

It imports the real model modules in app/models/ with a tiny in-process stand-in for SQLAlchemy and
Pydantic that RECORDS every table, column, type, index and unique constraint instead of talking to a
database — so it needs no installed dependencies and no database. The recorded schema is written out as
plain data. That data is the schema the application had when migrations were adopted; it is never
regenerated afterwards (later schema changes are ordinary, reviewed Alembic revisions).

Kept in the repository for audit and so the baseline can be re-derived and diffed:

    python alembic/tools/freeze_baseline.py            # prints a summary
    python alembic/tools/freeze_baseline.py --write    # rewrites app/db/baseline_schema.py

The set of tables the baseline contains is EXCLUDE-d explicitly (tables created by later revisions).
"""

import enum
import importlib
import os
import pkgutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))

# Tables that were added AFTER the baseline was taken: they belong to their own revisions, not to the baseline.
POST_BASELINE_TABLES = {"sync_events"}


# --------------------------------------------------------------------------------------
# A recording stand-in for SQLAlchemy / Pydantic (just enough for app/models/*.py)
# --------------------------------------------------------------------------------------

class _SaType:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs


def _type(name):
    return type(name, (_SaType,), {})


class _Enum(_SaType):
    pass


class _Column:
    def __init__(self, *args, **kwargs):
        self.name = args[0] if args and isinstance(args[0], str) else None
        # The models write both Column(String, ...) (a class) and Column(String(50), ...) / Column(SqlEnum(X), ...) (instances).
        types_ = [a() if isinstance(a, type) and issubclass(a, _SaType) else a for a in args
                  if isinstance(a, _SaType) or (isinstance(a, type) and issubclass(a, _SaType))]
        self.type = types_[0] if types_ else None
        self.kwargs = kwargs


class _UniqueConstraint:
    def __init__(self, *columns, name=None, **kw):
        self.columns, self.name = list(columns), name


class _Index:
    def __init__(self, name, *columns, unique=False, **kw):
        self.name, self.columns, self.unique = name, list(columns), unique


class _ForeignKey:
    def __init__(self, target, **kw):
        self.target = target


class _Meta(type):
    registry = []

    def __new__(mcs, name, bases, ns):
        cls = super().__new__(mcs, name, bases, ns)
        if ns.get("__tablename__"):
            columns = []
            for attr, value in ns.items():
                if isinstance(value, _Column):
                    value.name = value.name or attr
                    columns.append(value)
            _Meta.registry.append({"cls": cls, "table": ns["__tablename__"], "columns": columns, "args": ns.get("__table_args__", ())})
        return cls


def _install_stubs():
    sa = types.ModuleType("sqlalchemy")
    for t in ("String", "Integer", "DateTime", "Float", "Boolean", "Date", "Time", "Text"):
        setattr(sa, t, _type(t))
    sa.Enum, sa.Column, sa.UniqueConstraint, sa.Index, sa.ForeignKey = _Enum, _Column, _UniqueConstraint, _Index, _ForeignKey
    sa.event = types.SimpleNamespace(listens_for=lambda *a, **k: (lambda fn: fn))
    sa.inspect = lambda *a, **k: None
    sa.create_engine = lambda *a, **k: object()
    sa.text = lambda s: s
    ext = types.ModuleType("sqlalchemy.ext")
    decl = types.ModuleType("sqlalchemy.ext.declarative")
    decl.declarative_base = lambda: _Meta("Base", (), {})
    orm = types.ModuleType("sqlalchemy.orm")
    orm.sessionmaker = lambda *a, **k: object()
    orm.Session = object
    orm.relationship = lambda *a, **k: None
    pyd = types.ModuleType("pydantic")

    class BaseModel:
        def __init_subclass__(cls, **kw):
            pass

    pyd.BaseModel = BaseModel
    pyd.Field = lambda *a, **k: None
    for module in (sa, ext, decl, orm, pyd):
        sys.modules[module.__name__] = module
    sa.ext, sa.orm = ext, orm


# --------------------------------------------------------------------------------------
# Reading what the models declare
# --------------------------------------------------------------------------------------

def _enum_info(t):
    first = t.args[0] if t.args else None
    if isinstance(first, type) and issubclass(first, enum.Enum):
        # SQLAlchemy stores the member NAMES (not values) unless told otherwise.
        return {"name": t.kwargs.get("name") or first.__name__.lower(), "values": [m.name for m in first]}
    return {"name": t.kwargs.get("name"), "values": [str(v) for v in t.args]}


def _scalar_default(column):
    """A plain scalar default usable as `DEFAULT <literal>` when catching an OLD table up (callables are skipped,
    exactly as the previous lightweight migrator did)."""
    d = column.kwargs.get("default")
    if isinstance(d, enum.Enum):
        return d.name
    if isinstance(d, (bool, int, float, str)):
        return d
    return None


def _column_info(table, column):
    kw = column.kwargs
    t = column.type
    pk = bool(kw.get("primary_key", False))
    info = {
        "name": column.name,
        "type": "Enum" if isinstance(t, _Enum) else type(t).__name__,
        "nullable": False if pk else bool(kw.get("nullable", True)),
        "primary_key": pk,
        "unique": bool(kw.get("unique", False)),
        "index": bool(kw.get("index", False)),
        "default": _scalar_default(column),
    }
    if info["type"] == "String" and t.args and isinstance(t.args[0], int):
        info["length"] = t.args[0]
    if isinstance(t, _Enum):
        info["enum"] = _enum_info(t)
    return info


def record_schema():
    _install_stubs()
    sys.path.insert(0, BACKEND)
    import app.models as models_pkg  # noqa: E402
    for module in pkgutil.iter_modules(models_pkg.__path__):
        importlib.import_module(f"app.models.{module.name}")

    tables = []
    for entry in _Meta.registry:
        name = entry["table"]
        if name in POST_BASELINE_TABLES:
            continue
        columns = [_column_info(name, c) for c in entry["columns"]]
        uniques, indexes = [], []
        for arg in entry["args"] if isinstance(entry["args"], tuple) else ():
            if isinstance(arg, _UniqueConstraint):
                uniques.append({"name": arg.name, "columns": arg.columns})
            elif isinstance(arg, _Index):
                indexes.append({"name": arg.name, "columns": arg.columns, "unique": arg.unique})
        for c in columns:
            if c["index"]:
                indexes.append({"name": f"ix_{name}_{c['name']}", "columns": [c["name"]], "unique": c["unique"]})
        tables.append({"name": name, "columns": columns, "unique_constraints": uniques, "indexes": indexes})
    return sorted(tables, key=lambda t: t["name"])


# --------------------------------------------------------------------------------------

HEADER = '''"""
FROZEN baseline schema — the database schema as it stood when Alembic was adopted (release 71).

THIS FILE IS DATA AND IS NEVER REGENERATED OR EDITED. It describes what revision 0001 creates; every
later schema change is a new, reviewed revision under alembic/versions/. It was produced mechanically from
the model source by alembic/tools/freeze_baseline.py (no database involved), so it cannot contain a typo.

Per table: columns (type, nullability, key/index flags), the plain-scalar DEFAULT used only when an OLD
table is caught up with a column it lacks, unique constraints and indexes. Enum columns store the member
NAMES, as SQLAlchemy's Enum does.
"""

'''


def render(tables):
    def lit(value):
        return repr(value)

    lines = [HEADER, "BASELINE_TABLES = [\n"]
    for t in tables:
        lines.append("    {\n")
        lines.append(f"        \"name\": {lit(t['name'])},\n")
        lines.append("        \"columns\": [\n")
        for c in t["columns"]:
            lines.append(f"            {lit(c)},\n")
        lines.append("        ],\n")
        lines.append(f"        \"unique_constraints\": {lit(t['unique_constraints'])},\n")
        lines.append(f"        \"indexes\": {lit(t['indexes'])},\n")
        lines.append("    },\n")
    lines.append("]\n")
    return "".join(lines)


if __name__ == "__main__":
    schema = record_schema()
    print(f"{len(schema)} tables, {sum(len(t['columns']) for t in schema)} columns, "
          f"{sum(len(t['indexes']) for t in schema)} indexes, {sum(len(t['unique_constraints']) for t in schema)} unique constraints, "
          f"{sum(1 for t in schema for c in t['columns'] if c.get('enum'))} enum columns")
    if "--write" in sys.argv:
        target = os.path.join(BACKEND, "app", "db", "baseline_schema.py")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as handle:
            handle.write(render(schema))
        print("wrote", target)
