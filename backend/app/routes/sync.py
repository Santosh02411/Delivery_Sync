"""
The /sync endpoint — this is what the client calls when connectivity
returns, sending a batch of records that were saved offline in IndexedDB.

For each record, conflict resolution is applied (see services/conflict_resolver.py).
The response tells the client the FINAL state of each record, so the client
can update its local IndexedDB copy if the server's version won a conflict.
"""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import List

from app.db.session import get_db
from app.models.delivery import DeliveryRecordOut, DeliveryStatus
from app.models.user import UserDB
from app.routes.auth import get_current_user
from app.services.conflict_resolver import normalize_to_naive_utc
from app.services.sync_processor import process_sync_record
from app.services.sync_support import OUTCOME_ERROR, derive_event_id
from app.services.delivery_audit import record_change_decision
from app.services.rate_limiter import limiter
from app.services.security import record_security_event
from datetime import datetime, timedelta

# Upper bound on one batch — an offline agent's backlog is realistically a
# few dozen records; this just stops a single request from being used to
# hold a worker/DB connection for an unbounded amount of work.
MAX_RECORDS_PER_BATCH = 200

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sync"])


class SyncRecordIn(BaseModel):
    """Shape of each record sent in a sync batch from the client."""
    id: str
    agent_id: str
    order_id: str
    status: DeliveryStatus
    notes: str | None = None
    location_note: str | None = None
    created_at: datetime
    updated_at: datetime
    zone: str | None = None
    latitude: str | None = None
    longitude: str | None = None
    expected_by: datetime | None = None
    slot_start: datetime | None = None
    slot_end: datetime | None = None
    org_id: str | None = None
    proof_of_delivery: str | None = None
    customer_email: str | None = None
    customer_phone: str | None = None

    # Failed-delivery reason (see models/failed_delivery_reason.py).
    # NOT hard-enforced here the way update_delivery() enforces it —
    # /sync is best-effort and processes a whole
    # offline batch; rejecting a record over a missing reason code
    # would strand it retrying forever with no way for the agent to
    # fix it until they're back online anyway. If present, it's
    # recorded on the attempt log; if absent, the attempt is still
    # logged, just without a reason (visible as a gap for a dispatcher
    # to follow up on, rather than lost data).
    reason_code_id: str | None = None
    is_partial: bool = False
    partial_notes: str | None = None

    # The client's local edit counter for this record (see services/syncRevision.js on the
    # web, mobile/src/services/syncRevision.js). The server never uses it to decide anything;
    # it is handed back in `acknowledged` so the client can tell WHICH version of its local
    # record this response answers, and not overwrite a newer edit made while the request was
    # in flight.
    local_revision: int | None = Field(default=None, ge=0)

    # The server-managed `version` of the delivery this change was based on — what the device last READ
    # from the server (every delivery the API returns carries it). REQUIRED for an existing delivery:
    # it is how the server tells "nothing changed since you looked" from "someone changed it since", and
    # decides by that — not by comparing timestamps. See services/conflict_resolver.py.
    base_version: int | None = Field(default=None, ge=0)

    # IDEMPOTENCY KEY: a UUID the device generates ONCE when the agent makes this edit and re-sends unchanged on
    # every retry. The same (user, event_id) is applied at most once: a resend returns the original outcome with
    # `replayed: true` and the delivery's current state instead of being applied again. Optional — older clients
    # that omit it are still deduplicated when they send `local_revision` (the server derives a key from the
    # edit's content), and otherwise behave exactly as before.
    event_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class SyncRequest(BaseModel):
    records: List[SyncRecordIn] = Field(max_length=MAX_RECORDS_PER_BATCH)
    # The device's clock at the moment it sent this request. Comparing it with the server's clock tells the
    # server how far off the device clock is, so event times recorded while offline can be corrected to
    # real time. Optional (older clients simply don't get the correction).
    client_sent_at: datetime | None = None


class SyncRecordResult(BaseModel):
    """The explicit outcome for ONE record of the batch (see services/sync_support.py for the full contract)."""
    id: str
    event_id: str | None = None
    # applied | reconciled | already_applied | rejected | error
    outcome: str
    # True when this exact event had already been decided and the stored outcome was returned (nothing re-applied).
    replayed: bool = False
    code: str | None = None        # rejected / error: machine-readable reason
    message: str | None = None
    retryable: bool | None = None  # error only: True = send it again later; False = it will not succeed as sent
    # Best-effort actions run AFTER the record was saved: [{"name", "status": "done" | "failed"}].
    side_effects: List[dict] = []


class SyncResponse(BaseModel):
    # The classic fields (unchanged; existing clients use only these) ...
    resolved_records: List[DeliveryRecordOut] = []
    errors: List[dict] = []
    conflicts: List[dict] = []
    # [{id, local_revision, event_id}] for every resolved record that was sent with a revision.
    acknowledged: List[dict] = []
    # ... and the explicit per-record contract: exactly one entry per request record, in request order.
    results: List[SyncRecordResult] = []


CLOCK_OFFSET_TOLERANCE = timedelta(seconds=60)  # within this, trust the device clock as-is
CLOCK_OFFSET_MAX = timedelta(days=7)            # beyond this the number is nonsense (or an attack): ignore it


def _estimate_clock_offset(client_sent_at: datetime | None) -> timedelta | None:
    """Server time minus device time, or None if the device clock is close enough (or unusable)."""
    if client_sent_at is None:
        return None
    offset = datetime.utcnow() - normalize_to_naive_utc(client_sent_at)
    if abs(offset) <= CLOCK_OFFSET_TOLERANCE or abs(offset) > CLOCK_OFFSET_MAX:
        return None
    return offset


@router.post("/sync", response_model=SyncResponse)
@limiter.limit("30/minute")
def sync_records(
    request: Request,
    payload: SyncRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Accepts a batch of offline records and resolves each one against the
    server's current state. Returns the final resolved version of every
    record so the client can reconcile its local IndexedDB copy.

    AUTHENTICATED: requires `Authorization: Bearer <access token>` (401
    otherwise). The caller's identity and organization are taken from
    that token — `agent_id` / `org_id` in the payload are never trusted
    as identity. See services/conflict_resolver.py for the per-record
    rules (agents: only their own assigned deliveries, limited status
    changes; dispatchers/admins: their own org, no reassignment).

    THE CONTRACT (details: services/sync_support.py, docs/SYNC_CONTRACT.md)
      * The batch is PARTIAL SUCCESS: each record is authorized and processed independently, and one bad
        record never affects the others. The request answers 200 with one explicit entry per record in
        `results` (outcome: applied | reconciled | already_applied | rejected | error); a record that
        could not be considered is also listed in `errors` (with a machine-readable `code`) and stays
        "pending" on the client, so no data is lost, just delayed.
      * Each record is ALL-OR-NOTHING: its delivery change, history entry, delivery attempt, audit decision
        and idempotency row are committed in one transaction, or not at all.
      * Each record is IDEMPOTENT per `event_id`: re-sending an already-decided event applies nothing a
        second time and returns the original outcome with `replayed: true`.
      * Notifications, live-tracking broadcasts, return-pickup completion and RTO checks run AFTER the
        commit; a failure there is reported in that record's `side_effects`, never as a failed record.

    WHICH CHANGE WINS is decided by the server-managed `version`, not by timestamps: every record
    carries `base_version` (the version the device last read). A change based on the current version
    is applied if the central transition policy allows it ("accepted"); one based on an older version
    is applied only if it is still valid against the CURRENT state ("reconciled" — e.g. a dispatcher
    only edited a note meanwhile); otherwise it is refused and returned in `conflicts` with a code,
    a message, and both versions (e.g. an agent who was offline can't revive a delivery a dispatcher
    cancelled, however recent the agent's timestamp is). The server's copy of a refused record comes
    back in `resolved_records` so the device replaces its local one. Every decision — accepted,
    reconciled, rejected — is written to the delivery change audit log. Event TIMES are corrected
    for a wrong device clock using `client_sent_at`. See services/conflict_resolver.py.

    Rate limited to 30 requests/minute per IP — generous enough for normal
    usage (the frontend auto-syncs at most every 15 seconds, i.e. 4/minute,
    plus occasional manual "Sync Now" clicks).
    """
    resolved = []
    errors = []
    conflicts = []
    acknowledged = []
    results = []
    denied = []  # authorization refusals (not ordinary validation errors), for the security log
    clock_offset = _estimate_clock_offset(payload.client_sent_at)

    def audit_refusal(record: SyncRecordIn, code: str, message: str) -> None:
        """Rejected before it could be considered at all (not yours, invalid, ...): still leave a trace."""
        try:
            record_change_decision(
                db, org_id=current_user.org_id, delivery_id=record.id, order_id=record.order_id,
                outcome="rejected", source="offline_sync", actor=current_user, code=code, message=message,
                base_version=record.base_version, status_after=record.status,
                client_occurred_at=normalize_to_naive_utc(record.updated_at),
                clock_offset_seconds=clock_offset.total_seconds() if clock_offset else None,
            )
        except Exception:  # noqa: BLE001 - a failing audit write must not break the rest of the batch
            db.rollback()
            logger.exception("Could not write the audit entry for refused sync record %s", record.id)

    for record in payload.records:
        data = record.model_dump()
        revision = data.pop("local_revision")  # bookkeeping for the client; not a DeliveryRecordDB column
        # The idempotency key: the client's event_id, or (older clients) one derived from the edit itself.
        event_id = data.pop("event_id") or derive_event_id(current_user.id, data, revision)

        # ONE record = ONE transaction, and nothing it raises can escape (see services/sync_processor.py):
        # a failing record becomes an `error` result and the loop simply continues with the next one.
        result = process_sync_record(data, db, current_user, clock_offset, event_id)
        results.append(result.as_response())

        if result.outcome == OUTCOME_ERROR:
            info = result.error
            errors.append({"id": record.id, "error": info["message"], "code": info["code"]})
            if info["denied"]:
                denied.append(f"{info['code']}:{record.id}")
            if info["audit"]:
                audit_refusal(record, info["code"], info["message"])
            continue

        resolved.append(result.record)
        if revision is not None:
            acknowledged.append({"id": record.id, "local_revision": revision, "event_id": event_id})
        if result.conflict:
            conflicts.append(result.conflict)

    if denied:
        # One audit entry per batch (not per record) so a replayed bad
        # batch can't flood the security log; visible in the Security dashboard.
        record_security_event(
            db, current_user.id, current_user.org_id, "sync_rejected",
            detail=f"{len(denied)} record(s) refused: " + ", ".join(denied[:10]) + (" ..." if len(denied) > 10 else ""),
        )

    return {
        "resolved_records": resolved, "errors": errors, "conflicts": conflicts,
        "acknowledged": acknowledged, "results": results,
    }
