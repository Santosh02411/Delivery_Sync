# Security & Access

## 1. User Roles
| Role | Access |
|------|--------|
| Delivery Agent | Can create/update delivery records assigned to them |
| Dispatcher | Can view all delivery records and their statuses |

## 2. Authentication (v1 scope)
- Simple token-based authentication (JWT) issued on login
- Each agent's requests are tagged with their `agent_id` from their token —
  agents cannot spoof another agent's ID
- Dispatcher accounts have a separate role flag to access the dashboard/list-all
  endpoint

## 3. Data Validation
- All incoming sync payloads validated against the `DeliveryRecord` schema
  (FastAPI + Pydantic handles this automatically)
- Reject malformed timestamps or unknown status values before they reach the
  database

## 4. Data Integrity During Sync
- Each record's `id` is a client-generated UUID to avoid collisions across
  devices
- Server never trusts client `sync_status` — it's a client-side-only field and
  recalculated after each successful sync

## 5. What's Explicitly Out of Scope (v1)
- Encryption at rest (SQLite file is not encrypted in v1 — acceptable for a
  demo project, would be required before any real production use)
- Rate limiting / abuse protection
- Multi-tenant data isolation (only one "organization" assumed for now)

## 6. Notes for Future Hardening
If this were to move toward production use, next steps would include:
HTTPS enforcement, hashed/salted credential storage, rate limiting on the
`/sync` endpoint, and audit logging of who changed what and when.


## Offline sync endpoint (`POST /sync`) — authenticated

`/sync` previously accepted unauthenticated batches and trusted `agent_id`
from the payload. It now requires `Authorization: Bearer <access token>`.

- **Identity and tenant come from the token**, never the payload. `org_id`
  in a record is ignored; `agent_id` must equal the caller for agents.
- **Agents** may update only deliveries currently assigned to them, in their
  own org. They cannot create deliveries, cancel, move one back to
  `pending`, or change a delivery that is already `delivered`/`cancelled`.
  "Doesn't exist", "someone else's" and "other org's" all return the same
  `not_assigned` error, so delivery IDs can't be probed.
- **Dispatchers/admins** may create deliveries for an agent in their own org
  and update any delivery in their own org. Sync never reassigns an existing
  delivery (use `PATCH /deliveries/{id}/assign-agent`).
- **Each record is authorized independently**; refusals come back in `errors`
  with a machine-readable `code` (`identity_mismatch`, `not_assigned`,
  `not_found`, `invalid_agent`, `transition_not_allowed`, `invalid_record`).
- Batches are capped at 200 records; `updated_at`/`created_at` more than
  15 minutes in the future are clamped to now so a forged timestamp can't win
  last-write-wins indefinitely.
- Refused batches are logged once per batch as a `sync_rejected` security event.
- Clients: web and mobile send the access token; on 401 they keep records
  queued (nothing is lost), refresh the session, and retry on the next cycle.
  The service-worker background sync uses the last token the app stored and
  simply retries later if it has expired.

Tests: `backend/tests/test_sync_security.py`.
