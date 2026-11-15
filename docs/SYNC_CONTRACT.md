# POST /sync — the contract

For clients (web, mobile, service worker) and for anyone changing `routes/sync.py`,
`services/sync_processor.py`, `services/conflict_resolver.py`. Enforced by `tests/test_sync_contract.py`
and `tests/test_sync_support.py`.

## The decisions

| Question | Decision | Why |
|---|---|---|
| Is a **batch** all-or-nothing? | **No — partial success.** Each record is independent; the request answers `200` with one result per record. | An offline backlog is dozens of unrelated deliveries; one invalid record must not block or undo the others. |
| Is a **record** all-or-nothing? | **Yes.** The delivery change, its history entry, the delivery attempt, the audit decision and the idempotency row are committed in **one transaction**, or not at all. | No state where a delivery says "delivered" but has no history/attempt/audit. (Before: five separate commits.) |
| Are **notifications / broadcasts / return-pickup completion / RTO check** part of that transaction? | **No — follow-ups after the commit**, best-effort, each reported in `side_effects`. A failure never undoes the saved change. | They talk to the outside world or run their own transactions; rolling back a delivery because SMTP is down would be wrong. Return-pickup completion and the RTO check are idempotent and are re-run on a replay. |
| Can a record be sent twice safely? | **Yes — idempotent per `event_id`.** | Responses get lost; devices retry. |

## Request
Each record may carry `event_id`: a UUID the device generates **once, when the agent makes the edit**, and re-sends
unchanged on every retry (the web/mobile clients do this; "Try again" on a blocked record keeps the same id). 8–128
characters of `[A-Za-z0-9._:-]`. Older clients that omit it but send `local_revision` get a server-derived key built from
the edit's content (so two different edits can't collide). Without either, no deduplication — as before.

## Response
```jsonc
{
  "results": [                       // exactly one per request record, in request order
    { "id": "...", "event_id": "...", "outcome": "applied", "replayed": false,
      "side_effects": [ {"name": "customer_notification", "status": "done"}, ... ] },
    { "id": "...", "outcome": "error", "code": "not_assigned", "message": "...", "retryable": false },
    { "id": "...", "outcome": "rejected", "code": "stale_version", "message": "..." }
  ],
  // unchanged classic fields (existing clients read only these):
  "resolved_records": [...], "errors": [...], "conflicts": [...], "acknowledged": [...]
}
```
| outcome | meaning | in `resolved_records`? |
|---|---|---|
| `applied` | accepted and saved (or the delivery was created) | yes |
| `reconciled` | based on an older version but still valid; saved | yes |
| `already_applied` | nothing to do: this exact change was already applied | yes |
| `rejected` | refused as a conflict (see `conflicts[]`); the server's copy is returned | yes (server copy) |
| `error` | couldn't be considered at all; `retryable` says whether to resend | no (listed in `errors`) |

Error codes: `not_assigned`, `identity_mismatch`, `not_found`, `invalid_agent`, `invalid_record`, `base_version_required`
(permanent); `concurrent_update`, `pod_required`, `server_error` (retryable). `pod_required` means "waiting for proof of
delivery to be uploaded" — clients keep the record pending without counting retries.

## Idempotency rules
- The **same `(user, event_id)`** is applied **at most once**. A resend returns the **original outcome** with `replayed: true` and the delivery's **current** state (so the device converges even if the first response was lost). Access is re-checked on a replay.
- The ledger (`sync_events`, unique on `(actor_user_id, event_id)`) is written **in the same transaction** as the change. Two requests racing with the same event: the database lets exactly one in; the other rolls back and replays.
- **Recorded:** decisions about the change — `applied`, `reconciled`, `already_applied`, `rejected`.
- **Not recorded:** refusals that depend on changeable state — authorization failures, validation errors, `pod_required`, `concurrent_update`, `server_error`. A retry is evaluated afresh (e.g. once the dispatcher assigns the delivery, the same event works).
- A record that fails with an unexpected exception is rolled back completely and reported as `server_error` (retryable); the rest of the batch is unaffected. Internals are never sent to the client.

## Not covered
The online `PATCH /deliveries/{id}` path still commits history/attempt/audit separately; the helpers now accept
`commit=False`, so converting it the same way is straightforward. Ledger rows are not purged automatically (small; add a
retention job if the table grows large).
