# Database migrations (Alembic)

Schema changes are version-controlled in `backend/alembic/versions/`. The old mechanism
(`create_all()` + `app/db/migrate.py` adding missing columns at every start) could only add columns: it could
not create indexes or constraints on existing tables, alter a column, transform data, or roll anything back,
and nothing recorded which schema a database was at.

## What exists

| Revision | What it does |
|---|---|
| `0001` baseline | The schema as of release 71, from the **frozen** description in `app/db/baseline_schema.py` (71 tables). On a fresh database it creates everything. On an existing database it **only adds what is missing** — never drops or alters — so old databases join the history with all data intact; **no `alembic stamp` needed**. No downgrade (it would drop every table): restore a backup instead. |
| `0002` sync_events | The idempotency ledger for `/sync` (see `docs/SYNC_CONTRACT.md`). Additive. Downgrade drops the table. |

`backend/alembic/tools/freeze_baseline.py` produced the baseline mechanically from the model source (it needs no
database). The baseline file is data and is never edited or regenerated.

## Running it

```sh
cd backend
alembic current          # which revision is this database at?
alembic upgrade head     # bring it to the latest
alembic downgrade 0001   # undo later revisions (never below 0001)
alembic history          # the chain
```

**At startup the app upgrades itself** (`main.py` → `app/db/migrations.py`), so local development and single-instance
deployments need nothing extra. Controls:

| Variable | Meaning |
|---|---|
| `DB_AUTO_MIGRATE=0` | Don't touch the schema at startup; run `alembic upgrade head` as a release step (Render: `preDeployCommand: alembic upgrade head`; Docker: a one-off `docker compose run backend alembic upgrade head`). A behind database logs a loud warning. |
| `DB_REQUIRE_HEAD=1` | With `DB_AUTO_MIGRATE=0`: refuse to start if the schema is out of date. |

Several instances starting at once are safe on PostgreSQL: `env.py` holds an advisory lock for the duration, so one
upgrades and the rest wait. Before upgrading a **SQLite** file that has data, a consistent copy is written next to it
(`<db>.pre-<revision>-<time>.bak`). For **PostgreSQL** take a backup yourself first (`pg_dump -Fc`, or a managed-database snapshot).

`alembic upgrade head --sql` (offline SQL generation) is **not supported**: the "if missing" checks need a live database.

## Writing a new migration

1. Change the model in `app/models/`.
2. `alembic revision --autogenerate -m "add delivery_notes index"` and **read what it generated** — autogenerate is a draft.
3. Make it safe for databases that were kept up to date by the old mechanism: use the helpers in
   `app/db/migration_helpers.py` (`add_column_if_missing`, `create_index_if_missing`, `create_table_if_missing`, `drop_*_if_exists`).
   Every revision must be harmless to run twice.
4. Write a real `downgrade()`, or raise with the rollback plan (as `0001` does).
5. `pytest tests/test_migrations.py` — the **drift guard** fails if a model wants schema no migration creates.
6. Never edit a revision that has been applied anywhere. Add a new one.

### Patterns for the hard cases
- **Add a NOT NULL column to a populated table:** 1) add it nullable (with a server default if sensible), 2) backfill in the same revision or a `0003_backfill` data revision, 3) a *later* revision makes it NOT NULL. Never do all three at once on a big table.
- **Alter a column type / rename:** SQLite can't ALTER in place — `env.py` enables batch mode (`with op.batch_alter_table(...)`). Prefer expand → migrate data → contract across releases, so the previous release keeps working during a rolling deploy.
- **Data transformation:** do it in SQL (`op.execute`) in batches for large tables; make it idempotent (`WHERE new_col IS NULL`).
- **Add a unique index/constraint to existing data:** first find and fix duplicates (a query in the revision that fails with a clear message listing them), then create it. The baseline deliberately *skips* a missing unique index on an existing table rather than crash the app on startup.
- **Destructive change (drop column/table):** only after no released code reads it, in its own revision, with a backup taken first.

## Rollback planning
- **Application-level rollback** (the common case): deploy the previous code. Additive revisions (new tables/nullable columns) are compatible with it, so don't downgrade the schema just because you rolled back the code.
- **Schema rollback:** `alembic downgrade <revision>` — each revision documents what it loses in its docstring (`0002`: only the record of which events were already applied).
- **Below the baseline / disaster:** restore the pre-upgrade backup. This is why the backup is automatic for SQLite and a documented step for PostgreSQL.

## Verifying an upgrade on real data (the acceptance test)
Automated: `pytest tests/test_migrations.py` builds a database in the previous release's shape, fills it with users,
deliveries, orders, history and audit rows, upgrades it, and asserts every row is unchanged and the new schema is present.

On your own data, before the first production upgrade — on a **copy**:
```sh
pg_restore -d scratch latest.dump
DATABASE_URL=postgresql://.../scratch python scripts/verify_upgrade.py
```
It compares row counts and a checksum of every pre-existing column, per table, and exits non-zero on any difference.
Also try the app against the scratch database and look at the startup log (`baseline: created_tables / added_columns ...`).

## Known limits
- The baseline is "frozen data applied through SQLAlchemy", not hand-written DDL: it was verified by logic tests and by
  comparing with the model source, but the real-database tests (`test_migrations.py`) and the PostgreSQL path
  (enum types, advisory lock) should be run once in your own environment/staging before production.
- Legacy catch-up adds columns **nullable** (as the old startup migrator did), so a column the models call NOT NULL may be
  nullable in an old database until a revision tightens it.
- The test suite itself still builds its throwaway databases with `create_all()` (fast); the migrations are tested separately.
