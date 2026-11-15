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

## Customer two-factor authentication and account deletion

- **Customers have 2FA now**, same design as staff: an authenticator app (scan the QR code
  with Google Authenticator / Microsoft Authenticator / Authy) or a 6-digit code emailed at
  login, 8 one-time recovery codes (shown once, usable from the login screen), and the
  password is required to turn it off. Endpoints: `/customer/2fa/*`; login returns
  `requires_2fa` + a short-lived `challenge_token` (distinct claim from staff's, so the two
  can't be swapped). UI: customer sidebar > **Security (2FA)**.
- 2FA protects *password* logins. "Sign in with Google" relies on Google's own security, and
  Google-only accounts (no password) can't enable it until they add a password in Profile.
- **Delete account** for customers is on both **Profile** and **Privacy & Data**. Accounts
  with a password re-enter it; Google-only accounts type their email. Sessions, one-time codes
  and recovery codes are erased with the account.

## Offline sync: lost-update protection (web, mobile, service worker)

**Problem fixed.** A sync request used to send a snapshot of the pending queue and, when the
answer came back, overwrite the local record with the server's version and mark it synced. If the
agent edited the same parcel again while the request was in flight, that second edit was silently
replaced and never uploaded. Several syncs could also overlap (15-second timer, "online" event,
manual "Sync now", another tab, the background service worker).

**How it works now**
- **One sync at a time per user.** The web engine takes a named Web Lock
  (`delivery-sync:delivery_sync_db_<userId>`) that other tabs and the service worker take too; a
  second caller returns `{ skipped: true }` and the records simply wait for the next trigger.
  Browsers without Web Locks fall back to an in-memory flag. Mobile uses a single in-process flag.
- **Local revision numbers.** Every local edit increments `local_revision`, computed from the
  *stored* record inside the same transaction (so two quick edits never share a number, and a stale
  copy held in UI state can't reset it).
- **The revision travels with the request.** `POST /sync` accepts `local_revision` per record and
  returns `acknowledged: [{id, local_revision}]` for every resolved record.
- **Guarded write-back.** The server's answer is stored only if the local record is still at the
  revision that was sent. If it changed meanwhile it stays `pending` at its newer revision; the
  compare and the write happen in one IndexedDB transaction (web / service worker) or one
  serialized store operation (mobile). A record deleted locally mid-flight is not resurrected.
- **Retries never re-send stale data.** Every attempt, including each retry, re-reads the current
  queue. If an edit landed mid-flight, up to 3 immediate follow-up passes upload it instead of
  waiting for the next timer tick.
- **Server-pulled records** (`mergeAssignedDeliveries`, mobile `cacheDeliveries`) are also written
  with a check inside the write, so they can't clobber an edit made a moment earlier.
- **Mobile store write queue.** The mobile cache is one AsyncStorage key, so every change is
  read-whole-map / edit / write-whole-map. Those operations are now serialized; otherwise two
  overlapping writes could drop each other's changes.

Code: `frontend/src/services/syncRevision.js` (pure rules, copied to
`mobile/src/services/syncRevision.js`), `indexedDb.js`, `syncEngine.js`, `public/sw.js`,
`mobile/src/services/offlineStore.js`, `offlineSync.js`, `backend/app/routes/sync.py`.
Tests: `syncRevision.test.js`, `indexedDb.test.js` (needs `npm install` for `fake-indexeddb`),
`syncEngine.test.js`, mobile `offlineStore.test.js` / `offlineSync.test.js`, `test_sync_security.py`.

**Not covered:** the other offline queues (chat, proof-of-delivery, location pings, customer cart)
use their own stores and were not changed.

## Conflict resolution: versions and a central transition policy (not timestamps)

**Problem fixed.** Offline sync used last-write-wins on `updated_at`. A delayed device whose clock
looked "newer" could overwrite a later business decision — e.g. a dispatcher cancels a delivery while
the agent is offline, and the agent's later-looking "delivered" brought it back to life.

**How it works now**
- **Server-managed `version`** on every delivery (`DeliveryRecordDB.version`, returned by the API as
  `version`). It goes up by exactly one per saved change, from any code path (agent sync, online
  PATCH, dispatcher assign/cancel, customer cancel, RTO...). The database also makes each update
  conditional on the version that was read, so two concurrent requests can't silently overwrite each
  other (the loser gets `concurrent_update` and retries).
- **Clients send the version they read** as `base_version`. It is *required* on `POST /sync` for an
  existing delivery (`base_version_required` otherwise). Web, mobile and the service worker send the
  record's `version`; an edit saved before versions existed is sent as `0` ("unknown = oldest possible").
  `PATCH /deliveries/{id}` accepts it too and enforces it when present.
- **Three outcomes**, all written to the audit log:
  - **accepted** — `base_version` equals the current version and the policy allows the change;
  - **reconciled** — `base_version` is older, but the change is still valid against the *current*
    state (e.g. a dispatcher only edited the priority) and doesn't touch a finished delivery;
  - **rejected** — returned as a structured conflict: `{code, message, your_status, kept_status,
    kept_by, base_version, current_version}` (sync: in `conflicts`, with the server's copy in
    `resolved_records` so the device replaces its local one; online: HTTP 409 with `server_record`).
  A re-send of a change that was already applied (lost response) is a harmless no-op.
- **One transition policy** (`services/delivery_policy.py`) used by sync, the online PATCH and the
  customer cancel:
  | Actor | May do |
  |---|---|
  | Agent | from any non-final status to picked_up / out_for_delivery / delivered / failed_attempt; never cancel, never back to pending, never touch delivered/cancelled |
  | Dispatcher / admin | any change (can deliberately reinstate a cancelled delivery) |
  | Customer | cancel only while pending / picked_up |
  | **Any stale writer** | may **not** move a delivered/cancelled delivery, whatever its timestamp says |
- **Online PATCH also** now requires an agent to own the delivery (404 otherwise) and refuses
  policy violations (403).
- **Audit trail** (`delivery_change_audit` table): every accepted / reconciled / rejected decision with
  actor, base/before/after version, status before/after, and times. Per delivery:
  `GET /deliveries/{id}/change-audit` (dispatcher/admin). Whole org:
  `GET /admin/delivery-change-audit?outcome=&code=` (admin); UI: **Audit Log > Sync Decisions**.

**Timestamps now only describe when things happened, and they are real time**
- *Online* changes are stamped with the **server's** clock (the call is live; a phone's clock may be wrong).
- *Offline* changes carry the device's event time. The device also sends `client_sent_at` (its clock when
  uploading); the server compares it with its own clock and, if the device is off by more than a minute,
  shifts the event times by that difference, so an event on a phone that is 3 hours slow is still recorded
  at the real time it happened. Never later than now.
- Every record keeps both: `occurred_at` (corrected), `client_occurred_at` (raw device time) and
  `recorded_at` (server time when it was decided). Status history now also has `recorded_at`.
- SLA met/missed is judged on the real event time. The order of changes is the version sequence, never
  timestamp comparison.

Code: `backend/app/services/delivery_policy.py`, `conflict_resolver.py`, `delivery_audit.py`,
`models/delivery.py` (version), `models/delivery_audit.py`, `routes/sync.py`, `routes/deliveries.py`.
Tests: `test_delivery_versions.py`, `test_delivery_policy.py`, `test_sync_security.py`.

## Outbound webhooks: SSRF protection

**Problem fixed.** Webhook creation only checked that a URL started with `http://` or `https://`, then the
server made the request. A tenant admin could register `http://169.254.169.254/...` (cloud credentials),
`http://localhost:8000/...`, an internal hostname, or a public hostname whose DNS points at one of those, and
use the server to reach internal infrastructure.

**What happens now** (`backend/app/services/url_safety.py`)
- **HTTPS required** by default (`WEBHOOK_ALLOW_INSECURE_HTTP` opts into http; it never relaxes the address rules).
- No credentials in the URL, no whitespace/control characters, sane length and port.
- **Private, loopback, link-local, reserved, shared (CGNAT), multicast and documentation addresses are refused**,
  including IPv4 hidden inside IPv6 (`::ffff:127.0.0.1`, NAT64, 6to4) and decimal/hex/octal spellings of IPs.
  Internal host names (`localhost`, `*.internal`, `*.local`, `metadata.google.internal`, ...) are refused too.
- **DNS is checked when the URL is saved *and again at connection time*.** The delivery resolves the name once,
  refuses the host if *any* answer is non-public, and connects to that exact validated IP — there is no second
  lookup an attacker could poison (DNS rebinding). TLS still verifies the certificate against the real hostname.
- **Redirects are never followed** (a public host redirecting to the metadata address is the classic bypass).
  A 3xx is reported as a permanent failure with a readable reason.
- Proxy environment variables are ignored (a proxy would resolve names itself); `WEBHOOK_EGRESS_PROXY` is the explicit alternative.
- A blocked or redirected delivery is **not retried** (waiting can't fix it) and records `failure_code` /
  `last_error`, shown to the admin in the delivery list. Stored URLs saved before these rules are re-checked at send time.
- The response body is never downloaded.

**Second layer (infrastructure).** Network-level egress restrictions are deployment configuration, not code:
see `docs/WEBHOOK_EGRESS_HARDENING.md`. They are not enabled by this repository.

Tests: `backend/tests/test_webhook_ssrf.py` (private IPs and IP-in-IPv6 tricks, obfuscated IPs, save-time and
send-time DNS rebinding, connection pinning, redirects, mixed DNS answers, proxy env ignored, valid public
endpoint end to end, route-level 400s, legacy unsafe URL refused at send time).

## Offline sync: states, blocked records and honest outcomes (web, mobile, service worker)

**Problem fixed.** `runSync()` returned `success: true` even when the server refused records. A refused record
never became "synced", stayed "pending", and was re-sent every 15 seconds forever with no explanation. One
malformed record could also make the server reject the **whole** batch (HTTP 422), blocking every record behind
it. In the service worker, `Promise.allSettled()` consumed each task's result, so failed background operations
were swallowed and the browser was never asked to retry.

**Every record is in exactly one visible state**

| State | Stored? | Meaning | Re-sent automatically? |
|---|---|---|---|
| pending | yes | saved on this device, waiting (may show "will retry, attempt n of 5" and why) | yes |
| syncing | no (in-flight only) | being uploaded right now | — |
| synced | yes | confirmed by the server | — |
| conflicted | yes | the server did not apply your change (a newer one won); the server's copy is stored with an explanation until you acknowledge it | no |
| blocked | yes | cannot sync by itself; kept exactly as the agent left it, with a reason and recovery actions (**the dead-letter queue**) | **no** |

**Errors are classified** (`syncOutcome.js`): *permanent* (`not_assigned`, `not_found`, `identity_mismatch`,
`invalid_agent`, `invalid_record`, `base_version_required`) → blocked immediately; *retryable*
(`concurrent_update` and any code nobody anticipated) → attempt counted, blocked after 5 so nothing can loop
forever. Recovery: **Try again** (blocked after retries), **Discard my change** (removes it from this device
only), **Got it** (conflicts). Editing a blocked record again also starts it fresh. A routine server pull never
overwrites a blocked record or an unacknowledged conflict.

**Run outcomes are explicit:** `success` (nothing needs attention), `partial_success` (something settled, something
still needs attention — including any conflict), `failed` (nothing settled). `success: true` now means *only* that.

**A poisoned batch no longer blocks the rest.** An HTTP 400/413/422 for the whole request is deterministic, so the
engine finds the offending record (using the positions FastAPI reports, or by splitting the batch) and blocks only
it. Large backlogs go up in batches of 100. A wrong URL / 404 / 500 is treated as an outage and **never** blamed on a record.

**Service worker.** Task results are inspected, not discarded: a retryable failure (network, 5xx/429, IndexedDB error)
rejects the `sync` event so the browser retries; on its last chance the worker re-registers the sync. Expired sessions
are reported to open tabs instead of looping. Also fixed in the same code: a permanently invalid location ping no
longer blocks the pings behind it; a 403 on a chat message no longer blocks later messages; customer/dispatcher
actions are no longer **deleted** on a transient 5xx/429; the cart is only marked clean when its requests succeeded;
`swOpenExistingDb` stops the worker creating empty databases that would block the app's own upgrade.

**UI.** Web: "Sync status" panel (pending / syncing / synced / conflicted / blocked counts, every problem record
with its reason and buttons), state-aware badges, reason in the detail view. Mobile: the same panel on the delivery
list; blocked records stay listed even when the server no longer lists that delivery.

**Known limits.** Items in the *other* offline queues (chat, pings, customer/dispatcher actions) that the server
refuses permanently are still dropped (now counted and reported to open tabs) rather than moved to a recoverable
queue — they have no screen to review them. Repeated 5xx for one specific record is treated as an outage, not
attributed to that record (attributing it risks blocking real data during an outage).

Code: `frontend/src/services/syncOutcome.js` (rules, mirrored in `mobile/src/services/syncOutcome.js` and
`frontend/public/sw.js`), `syncEngine.js`, `indexedDb.js`, `mobile/src/services/offlineSync.js`, `offlineStore.js`,
`frontend/public/sw.js`, `components/SyncStatusPanel.jsx`. Tests: `syncOutcome.test.js` (incl. service-worker parity),
`syncEngine.test.js`, `serviceWorkerSync.test.js`, `indexedDb.test.js`; mobile `offlineSync.test.js`, `offlineStore.test.js`,
`syncOutcome.test.js`, `syncView.test.js`.

