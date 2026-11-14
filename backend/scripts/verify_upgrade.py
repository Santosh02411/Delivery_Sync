"""
Verify that upgrading a database to the latest schema loses nothing — run this against a COPY of production.

    # 1. restore a recent backup into a scratch database (never the live one), e.g.
    #      createdb scratch && pg_restore -d scratch latest.dump
    # 2. point this script at it:
    DATABASE_URL=postgresql://user:pass@host/scratch python scripts/verify_upgrade.py

For every table that existed before the upgrade it records the row count and a checksum over the values of the
columns that existed before, runs `upgrade_to_head`, and compares. Exit code 0 = nothing lost or altered; 1 = a
difference (printed). Rows are streamed, so large tables are fine. The upgrade is applied for real to the database
you point it at — which is the point, so use a copy.
"""

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

from app.db.migrations import migration_status, upgrade_to_head  # noqa: E402


def fingerprint(engine, table, columns):
    """(row count, order-independent checksum) over the given columns."""
    quoted = ", ".join(f'"{c}"' for c in columns)
    digest, count = 0, 0
    with engine.connect().execution_options(stream_results=True) as connection:
        for row in connection.execute(text(f'SELECT {quoted} FROM "{table}"')):
            digest ^= int.from_bytes(hashlib.sha256(repr(tuple(row)).encode()).digest()[:16], "big")  # XOR: row order doesn't matter
            count += 1
    return count, digest


def snapshot(engine):
    inspector = inspect(engine)
    result = {}
    for table in inspector.get_table_names():
        if table == "alembic_version":
            continue
        columns = [c["name"] for c in inspector.get_columns(table)]
        result[table] = (columns, fingerprint(engine, table, columns))
    return result


def main() -> int:
    url = os.environ.get("DATABASE_URL") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not url:
        print("Set DATABASE_URL (or pass it as the first argument) to a COPY of the database.")
        return 2
    engine = create_engine(url.replace("postgres://", "postgresql://", 1))

    before_status = migration_status(engine)
    print(f"Revision before: {before_status['current'] or '(none - pre-Alembic database)'}   latest: {before_status['head']}")
    before = snapshot(engine)
    print(f"{len(before)} tables, {sum(f[0] for _, f in before.values())} rows total")

    upgrade_to_head(engine, backup=False)

    after = snapshot(engine)
    problems = []
    for table, (columns, (count, digest)) in before.items():
        if table not in after:
            problems.append(f"table {table} disappeared")
            continue
        new_count, new_digest = fingerprint(engine, table, columns)      # compare ONLY the columns that existed before
        if new_count != count:
            problems.append(f"{table}: {count} rows before, {new_count} after")
        elif new_digest != digest:
            problems.append(f"{table}: same row count but the values of existing columns changed")
    added = sorted(set(after) - set(before))
    print(f"Revision after: {migration_status(engine)['current']}   new tables: {', '.join(added) or 'none'}")
    if problems:
        print("PROBLEMS:\n  " + "\n  ".join(problems))
        return 1
    print("OK: every existing table has the same rows with the same values.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
