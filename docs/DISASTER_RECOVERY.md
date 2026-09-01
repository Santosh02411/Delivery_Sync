# Disaster Recovery

This document describes how to recover Delivery Sync's data and
service after data loss, corruption, or an outage. It's written for
the actual deployment modes this project supports (see
`docs/DOCKER.md` and `backend/app/db/session.py`), not a generic
template.

## Scope and honest limitations

Delivery Sync supports two database backends, and this document is
direct about what's automated for each:

- **SQLite** (the default — `DATABASE_URL` unset): this is a single
  file (`backend/database.db`). Phase 18 (Monitoring & Reliability)
  implements real, working backup and verification for this mode —
  see below.
- **PostgreSQL** (`DATABASE_URL` set to a `postgres://`/`postgresql://`
  connection string): backup and restore is correctly the
  responsibility of `pg_dump`/`pg_restore` or your managed database
  provider's automated backups (RDS snapshots, Supabase backups,
  etc.), run OUTSIDE this application process. `POST
  /admin/monitoring/backups` will tell you this plainly rather than
  claim to back up a database it has no file-level access to.

## Backing up (SQLite)

1. In the admin UI: **Monitoring → Create Backup**, or:
   ```
   curl -X POST https://your-deployment/admin/monitoring/backups \
     -H "Authorization: Bearer <admin access token>"
   ```
2. This copies `backend/database.db` to
   `backend/backups/backup-<UTC timestamp>.db` and returns a SHA-256
   checksum of the copy.
3. **Copy that backup file off the server.** A backup that lives on
   the same disk as the original database survives corruption but not
   disk loss — download it, or better, script a periodic job (cron,
   a scheduled CI job, etc.) that runs the backup endpoint and then
   copies the resulting file to separate storage (S3, a different
   host, etc.). This project does not do that copy-off-host step for
   you.

### Verifying a backup

`GET /admin/monitoring/backups/{filename}/verify` (or the "Verify"
button in the admin UI) does two checks, not one:

1. Recomputes the file's SHA-256 checksum and compares it to the one
   recorded at backup time.
2. **Actually opens the file as a SQLite database** and runs a
   trivial query against it. A file that copied successfully but is
   truncated or corrupted mid-write is exactly the failure mode a
   checksum alone won't catch — only actually opening it as a
   database will.

Run verification periodically on your most recent backups, not just
once at creation time — disk-level bit rot can happen after the fact.

## Restoring (SQLite)

1. Stop the application (`docker compose down`, or however it's
   running).
2. Move the current (possibly corrupted/lost) `backend/database.db`
   aside, in case it's still partially useful for forensics:
   ```
   mv backend/database.db backend/database.db.broken
   ```
3. Copy the chosen backup file into place:
   ```
   cp backend/backups/backup-<timestamp>.db backend/database.db
   ```
4. Restart the application. `Base.metadata.create_all()` (see
   `main.py`) will add any newly-introduced tables from schema changes
   released since that backup was taken — it does not drop or alter
   existing tables, so the restored data is preserved.
5. Check `GET /health/db` responds `{"status": "ok"}` and spot-check a
   few known records via the admin UI.

## Restoring (PostgreSQL)

Use your provider's point-in-time-recovery or `pg_restore` against
your own `pg_dump` backups, following their documented procedure.
Once the database is restored, point `DATABASE_URL` at it and start
the application — `Base.metadata.create_all()` behaves the same way
described above.

## Migration safety

Schema changes across the phases of this project have all been
**additive**: new tables, and new nullable/defaulted columns on
existing tables (see `docs/FEATURE_LOG.md` — every phase's entry
documents this explicitly). `Base.metadata.create_all()` only ever
creates tables that don't exist yet; it never drops or alters an
existing one. This means:

- Upgrading to a newer version of this codebase against an existing
  database is safe to do without a backup-first step, in the sense
  that no existing data or column is ever dropped by the startup
  migration path.
- New nullable columns on an existing table (e.g. Phase 15's
  `ProductDB.cost_price`, Phase 16's organization branding fields)
  simply don't exist as a concept for rows written before that
  release — they read back as `NULL`/`None`, which every consumer of
  those fields is written to handle (see each phase's `FEATURE_LOG.md`
  entry for the specific "what happens to old rows" note).
- **Still take a backup before any schema/dependency upgrade anyway.**
  "The migration path is additive-safe" is a statement about this
  application's own code; it says nothing about human error, a bad
  `git checkout`, or a dependency upgrade that behaves unexpectedly.

There is no separate down-migration/rollback tooling in this project
— rolling back a bad deploy means restoring the pre-deploy backup, not
running a reverse migration.

## Alerting

Two channels currently exist for surfacing problems, both real and
already wired in — not a placeholder:

- **Security alerts** (Phase 17): `services/email.py`'s
  `send_security_alert_email()` fires on a suspicious login, a
  password change, 2FA being disabled, or an all-devices logout — sent
  to the affected user directly.
- **Monitoring dashboard** (Phase 18): `GET /admin/monitoring/status`
  surfaces database health, every background job's heartbeat/health,
  and live WebSocket connection counts in one call — the admin UI's
  Monitoring page polls this on load. There is no push-based alerting
  (e.g. paging an on-call engineer) implemented — that would need a
  real external alerting integration (PagerDuty, Slack webhook, etc.)
  with credentials this environment doesn't have, the same "bring your
  own credentials, or it no-ops" pattern already established for
  Razorpay/SMTP/push/Google geocoding elsewhere in this project. An
  admin checking the Monitoring page (or scripting a periodic call to
  `/admin/monitoring/status` and `/health/db`) is the current source
  of truth for "is anything wrong."
