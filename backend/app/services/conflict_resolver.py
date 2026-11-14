"""
Conflict resolution logic for syncing offline delivery records.

Which change wins is decided by the server-managed delivery `version` and the central transition policy
(not by timestamps) — see resolve_record() below. The transaction contract (each record all-or-nothing,
batch partial-success, idempotent per event_id) is in services/sync_support.py and docs/SYNC_CONTRACT.md.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session
from app.models.delivery import DeliveryRecordDB, DeliveryStatus
from app.models.delivery_history import DeliveryHistoryDB
from app.models.failed_delivery_reason import FailedDeliveryReasonDB
from app.models.user import UserDB, UserRole
from app.services.delivery_audit import record_change_decision
from app.services.delivery_policy import FINAL_STATUSES, STALE_VERSION, check_transition, check_stale_change
from app.services.history import record_history_entry
from app.services.delivery_attempts import record_delivery_attempt, ATTEMPT_OUTCOMES
from app.models.sync_event import SyncEventDB
from app.services.notifications import notify_customer_of_status_change
from app.services.sync_support import (
    FollowUp, run_follow_ups,
    OUTCOME_APPLIED, OUTCOME_RECONCILED, OUTCOME_ALREADY_APPLIED, OUTCOME_REJECTED,
)
from app.services.returns_workflow import handle_return_pickup_completion
from app.services.websocket_manager import broadcast_sync, tracking_room
from app.models.organization import OrganizationDB
from app.services.pod import org_requires_any_pod, pod_exists_for_delivery
from app.services.sla import assign_sla, classify_on_completion


def _log_synced_attempt(db: Session, db_record: DeliveryRecordDB, agent_id: str, status, is_partial: bool, reason_code_id: str | None, notes: str | None, attempted_at: datetime):
    """
    Logs a delivery_attempts row for a real attempt outcome synced from offline, mirroring what
    update_delivery() does for the online path. See routes/sync.py's SyncRecordIn docstring for why
    reason_code_id isn't hard-enforced here the way it is online.

    It joins the CALLER'S transaction (nothing is committed here) so the attempt is saved or discarded
    together with the delivery change. The RTO eligibility check commits and broadcasts on its own, so it is
    NOT run here: the caller runs it after its own commit, using the returned (logged, reason, outcome).
    """
    status_value = status.value if hasattr(status, "value") else status
    outcome = "partial_delivery" if (status_value == "delivered" and is_partial) else status_value
    if outcome not in ATTEMPT_OUTCOMES:
        return False, None, outcome

    reason = None
    if reason_code_id:
        reason = db.query(FailedDeliveryReasonDB).filter(
            FailedDeliveryReasonDB.id == reason_code_id,
            FailedDeliveryReasonDB.org_id == db_record.org_id,
        ).first()

    record_delivery_attempt(
        db, db_record, agent_id=agent_id, outcome=outcome,
        reason_code_id=reason.id if reason else None,
        reason_label=reason.label if reason else None,
        notes=notes, attempted_at=attempted_at,
        commit=False, run_rto_check=False,
    )
    return True, reason, outcome


# ---------------------------------------------------------------------------
# Authorization for offline-sync records
# ---------------------------------------------------------------------------
# /sync requires a valid access token (see routes/sync.py). The caller's
# identity and organization come ONLY from that token (`actor` below) —
# agent_id / org_id in the payload are never trusted as identity. Every
# record in a batch is authorized independently, so one forged or stale
# record is reported in the response's `errors` and cannot sink, or
# piggy-back on, the rest of the batch.
#
# Rules:
#   AGENT       may update only deliveries currently assigned to them, in
#               their own org. Payload agent_id must equal their own id.
#               May NOT create deliveries (online, only dispatchers can —
#               POST /deliveries), reassign, cancel, or move a delivery
#               that is already delivered/cancelled.
#   DISPATCHER/ may create deliveries for an agent in their own org and
#   ADMIN       update any delivery in their own org. Sync never changes
#               an existing delivery's assignment: reassignment goes
#               through PATCH /deliveries/{id}/assign-agent only.
#
# Agents get ONE generic error for "doesn't exist", "other agent's" and
# "other org's" so the endpoint can't be used to probe which delivery
# IDs exist elsewhere.

# Offline devices can have wrong clocks; tolerate some skew, but never let
# a record claim to be from the future — last-write-wins would otherwise
# let one forged far-future timestamp beat every legitimate update.
MAX_CLOCK_SKEW = timedelta(minutes=15)


class SyncRecordRefused(ValueError):
    """A record can't be considered right now. Subclasses ValueError so existing `except ValueError`
    handling keeps working; `code` is machine-readable for clients and logs. The base class is for refusals
    that are about the STATE of the world and may clear up on their own (e.g. `pod_required`)."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class SyncAuthorizationError(SyncRecordRefused):
    """A record was refused for authorization/validation reasons (not yours, wrong org, invalid agent...)."""


_NOT_ASSIGNED_MSG = "This delivery isn't assigned to you."


def _authorize_sync_record(db: Session, actor: UserDB, record_data: dict, existing: DeliveryRecordDB | None) -> None:
    """Raises SyncAuthorizationError unless `actor` may apply this record.
    Runs BEFORE anything is read back to the caller, so a rejected record
    never leaks another agent's/org's delivery data in the response."""
    if actor.role == UserRole.agent:
        if record_data["agent_id"] != actor.id:
            raise SyncAuthorizationError(
                "Record agent_id doesn't match the signed-in user.", "identity_mismatch"
            )
        if existing is None or existing.org_id != actor.org_id or existing.agent_id != actor.id:
            raise SyncAuthorizationError(_NOT_ASSIGNED_MSG, "not_assigned")
        return

    # dispatcher / admin
    if existing is not None:
        if existing.org_id != actor.org_id:
            raise SyncAuthorizationError("Delivery not found.", "not_found")
        return
    target = db.query(UserDB).filter(
        UserDB.id == record_data["agent_id"], UserDB.org_id == actor.org_id
    ).first()
    if not target or target.role != UserRole.agent:
        raise SyncAuthorizationError(
            "The selected agent doesn't exist in your organization.", "invalid_agent"
        )


def _clamp_future(dt: datetime) -> datetime:
    """Naive-UTC datetime -> no later than now (+ nothing) if it's beyond the skew allowance."""
    now = datetime.utcnow()
    return now if dt > now + MAX_CLOCK_SKEW else dt


def _normalize_to_naive_utc(dt: datetime) -> datetime:
    """
    Browsers send timestamps like '2026-07-22T19:10:46.276Z', which Python
    parses as timezone-AWARE (they carry a 'this is UTC' marker). But
    SQLite/SQLAlchemy stores and returns timezone-NAIVE datetimes (no
    marker at all). Python refuses to compare aware and naive datetimes
    directly, which is exactly the bug this caused.

    Fix: always convert to UTC first (in case it wasn't already), then
    strip the timezone marker, so every datetime we compare or store is
    naive UTC consistently.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


normalize_to_naive_utc = _normalize_to_naive_utc  # public name for routes/sync.py


def _event_time(client_time: datetime, clock_offset: timedelta | None) -> datetime:
    """
    The REAL time an offline event happened, as best the server can tell.

    A device that was offline reports the time on ITS clock, which may be minutes or hours off.
    routes/sync.py compares the device's clock at upload time with the server's and passes the
    difference as `clock_offset`; adding it back gives the event's real time. (Within a minute the
    device clock is trusted as-is.) Never later than now. This is only a description of when the
    event occurred — it plays no part in deciding which change wins (versions do).
    """
    now = datetime.utcnow()
    if clock_offset is not None:
        corrected = client_time + clock_offset
        return min(corrected, now)
    return _clamp_future(client_time)


def _winner_name(db: Session, delivery_id: str) -> str | None:
    entry = (
        db.query(DeliveryHistoryDB)
        .filter(DeliveryHistoryDB.delivery_id == delivery_id)
        .order_by(DeliveryHistoryDB.changed_at.desc())
        .first()
    )
    return entry.changed_by_display_name if entry else None


def _status_value(status):
    return status.value if hasattr(status, "value") else status


@dataclass
class SyncResolution:
    """The decision for ONE record, after its transaction has committed."""
    record: DeliveryRecordDB
    conflict: Optional[dict]
    outcome: str                                   # applied | reconciled | already_applied | rejected
    follow_ups: List[FollowUp] = field(default_factory=list)   # best-effort actions to run now that it is committed


def _check_rto(db: Session, record: DeliveryRecordDB, reason) -> None:
    """Imported lazily, like services/delivery_attempts.py does, to avoid a circular import."""
    from app.services.rto import check_rto_eligibility
    check_rto_eligibility(db, record, reason)


def _record_event(db: Session, *, event_id: Optional[str], actor: UserDB, delivery_id: str, outcome: str, result: Optional[dict] = None) -> None:
    """Adds the idempotency-ledger row to the CURRENT transaction (it commits, or rolls back, with the change)."""
    if not event_id:
        return
    db.add(SyncEventDB(
        org_id=actor.org_id, actor_user_id=actor.id, event_id=event_id, delivery_id=delivery_id,
        outcome=outcome, result_json=json.dumps(result) if result else None,
    ))


def resolve_record(
    record_data: dict,
    db: Session,
    actor: UserDB,
    clock_offset: timedelta | None = None,
    event_id: str | None = None,
) -> SyncResolution:
    """
    Decide what to do with ONE record from a client's offline sync batch, and save the decision.

    WHICH CHANGE WINS is decided by the server-managed `version`, never by comparing timestamps:

      * `base_version` (required for existing deliveries) is the version the client last READ.
      * base_version == current version  -> nothing changed since: apply, if the central transition
        policy (services/delivery_policy.py) allows it.            [audit outcome: accepted]
      * base_version != current version  -> the delivery changed since the client looked. The change is
        applied only if it is still valid against the CURRENT state and doesn't try to move a final
        (delivered/cancelled) delivery — e.g. an agent who was offline while a dispatcher cancelled
        the delivery cannot bring it back.                         [audit outcome: reconciled]
      * otherwise the change is refused and returned as a structured conflict (code, message, the
        versions involved, who made the winning change); the server's copy is returned so the client
        replaces its local one.                                    [audit outcome: rejected]

    TRANSACTION: whichever way it goes, everything this record writes — the delivery change, its history
    entry, the delivery attempt, the audit decision and the idempotency-ledger row — is committed in ONE
    transaction at the end, or (on any exception, which is re-raised) not at all; the caller rolls back.
    Nothing here commits in the middle. Side effects that talk to the outside world or run their own
    transactions (customer notification, live-tracking broadcast, return-pickup completion, RTO check) are
    NOT done here: they are returned as `follow_ups` for the caller to run after the commit, so a failure in
    one of them can never leave the delivery half-updated.

    Timestamps still describe WHEN the event happened (corrected for a wrong device clock, see
    _event_time) and are stored on the record, history and audit log.

    Returns a SyncResolution. `record` is the FINAL stored record (the client overwrites its local copy
    with it); `conflict` is None unless the change was refused. `actor` is the AUTHENTICATED caller, never
    taken from the payload. Raises ValueError (SyncAuthorizationError / SyncRecordRefused for refusals) for a
    record that can't be considered at all — services/sync_processor.py reports those per record.
    """
    client_updated_at = _normalize_to_naive_utc(record_data["updated_at"])
    client_created_at = _normalize_to_naive_utc(record_data["created_at"])
    record_data["created_at"] = _event_time(client_created_at, clock_offset)
    record_data["updated_at"] = occurred_at = _event_time(client_updated_at, clock_offset)
    offset_seconds = clock_offset.total_seconds() if clock_offset is not None else None

    base_version = record_data.pop("base_version", None)

    existing = db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.id == record_data["id"]
    ).first()

    # SECURITY: identity and tenant come from the authenticated `actor`, never from the payload.
    # Authorization happens before anything about the existing record can influence (or be
    # returned in) the response.
    _authorize_sync_record(db, actor, record_data, existing)

    if existing is not None and base_version is None:
        raise SyncAuthorizationError(
            "base_version is required: send the `version` of the delivery you last read.", "base_version_required"
        )

    agent_name = actor.display_name
    # org_id is NEVER trusted from the client — always the actor's own org.
    record_data["org_id"] = actor.org_id
    # Sync never reassigns: an existing delivery keeps its current agent. For a new (dispatcher-created)
    # record, the validated payload agent_id is the assignee. History is attributed to the real actor.
    if existing is not None:
        record_data["agent_id"] = existing.agent_id
    attempt_agent_id = record_data["agent_id"] or actor.id

    # reason_code_id isn't a column on DeliveryRecordDB (it lives on the delivery_attempts log).
    reason_code_id = record_data.pop("reason_code_id", None)

    def audit(outcome, *, code=None, message=None, before=None, after=None, status_before=None, status_after=None, delivery=None):
        record_change_decision(
            db, org_id=actor.org_id, delivery_id=record_data["id"], order_id=(delivery.order_id if delivery else record_data.get("order_id")),
            outcome=outcome, source="offline_sync", actor=actor, code=code, message=message,
            base_version=base_version, version_before=before, version_after=after,
            status_before=status_before, status_after=status_after,
            occurred_at=occurred_at, client_occurred_at=client_updated_at, clock_offset_seconds=offset_seconds,
            commit=False,  # part of this record's transaction; committed once, below
        )

    # ENFORCEMENT (Proof of Delivery): the OTHER place (besides routes/deliveries.py's online PATCH) a
    # delivery can end up "delivered". If the org requires POD and none has been captured yet, the
    # record is refused (reported per record, stays pending on the client) until POD has synced. This is a
    # refusal that clears up on its own, so it has its own code (`pod_required`) and is retryable.
    if record_data.get("status") == DeliveryStatus.delivered:
        org = db.query(OrganizationDB).filter(OrganizationDB.id == actor.org_id).first()
        if org and org_requires_any_pod(org) and not pod_exists_for_delivery(db, record_data["id"], actor.org_id):
            raise SyncRecordRefused(
                "Proof of delivery is required before this delivery can be marked as delivered. "
                "It will sync automatically once proof of delivery has been captured.",
                "pod_required",
            )

    if not existing:
        # A new record (dispatcher/admin only — see _authorize_sync_record): just insert it.
        new_record = DeliveryRecordDB(**record_data)
        assign_sla(db, new_record)
        if new_record.status == DeliveryStatus.delivered:
            classify_on_completion(new_record, new_record.updated_at)
        db.add(new_record)
        db.flush()  # assigns the version / defaults; still inside this record's transaction

        record_history_entry(
            db, delivery_id=new_record.id, changed_by_user_id=actor.id, changed_by_display_name=agent_name,
            old_status=None, new_status=new_record.status, changed_at=new_record.created_at,
            note="Created via offline sync", commit=False,
        )
        logged, reason, attempt_outcome = _log_synced_attempt(
            db, new_record, agent_id=attempt_agent_id, status=new_record.status,
            is_partial=record_data.get("is_partial", False), reason_code_id=reason_code_id,
            notes=record_data.get("notes") or record_data.get("partial_notes"),
            attempted_at=new_record.created_at,
        )
        audit("accepted", message="Created via offline sync", after=new_record.version,
              status_after=new_record.status, delivery=new_record)
        _record_event(db, event_id=event_id, actor=actor, delivery_id=new_record.id, outcome=OUTCOME_APPLIED)
        db.commit()  # <- the ONE commit for this record
        db.refresh(new_record)

        follow_ups = []
        if logged and attempt_outcome == "failed_attempt":
            follow_ups.append(FollowUp("rto_eligibility", lambda: _check_rto(db, new_record, reason)))
        return SyncResolution(new_record, None, OUTCOME_APPLIED, follow_ups)

    # ---------- The record exists: decide by VERSION and the central policy ----------
    version_before = existing.version
    status_before = existing.status
    incoming_status = record_data["status"]
    actor_kind = _status_value(actor.role)
    stale = base_version != version_before

    verdict = (
        check_stale_change(actor_kind, status_before, incoming_status)
        if stale else check_transition(actor_kind, status_before, incoming_status)
    )

    if not verdict.allowed:
        audit("rejected", code=verdict.code, message=verdict.message, before=version_before, after=version_before,
              status_before=status_before, status_after=incoming_status, delivery=existing)
        conflict = {
            "id": existing.id,
            "order_id": existing.order_id,
            "code": verdict.code,
            "message": verdict.message,
            "your_status": _status_value(incoming_status),
            "your_updated_at": occurred_at.isoformat(),
            "kept_status": _status_value(existing.status),
            "kept_updated_at": existing.updated_at.isoformat(),
            "kept_by": _winner_name(db, existing.id),
            "base_version": base_version,
            "current_version": version_before,
        }
        # A refusal is a DECISION about this event: remember it, so a resend gets the same answer.
        _record_event(db, event_id=event_id, actor=actor, delivery_id=existing.id, outcome=OUTCOME_REJECTED, result={"conflict": conflict})
        db.commit()
        return SyncResolution(existing, conflict, OUTCOME_REJECTED)

    if stale and incoming_status == status_before and status_before in FINAL_STATUSES:
        # A re-send of something already applied (e.g. the response was lost): nothing to do, and
        # re-applying would only re-run side effects. Report the server's copy back, no conflict.
        audit("reconciled", code="already_applied", message="This change had already been applied.",
              before=version_before, after=version_before, status_before=status_before,
              status_after=incoming_status, delivery=existing)
        _record_event(db, event_id=event_id, actor=actor, delivery_id=existing.id, outcome=OUTCOME_ALREADY_APPLIED)
        db.commit()
        return SyncResolution(existing, None, OUTCOME_ALREADY_APPLIED)

    # ---------- Apply ----------
    old_status = existing.status
    existing.status = incoming_status
    existing.notes = record_data.get("notes")
    existing.location_note = record_data.get("location_note")
    # "latest event time" never moves backwards, even if this (delayed) event happened earlier.
    existing.updated_at = max(existing.updated_at, occurred_at)
    if record_data.get("proof_of_delivery") is not None:
        existing.proof_of_delivery = record_data.get("proof_of_delivery")
    if existing.status == DeliveryStatus.delivered:
        existing.is_partial = record_data.get("is_partial", False)
        existing.partial_notes = record_data.get("partial_notes") if existing.is_partial else None
        classify_on_completion(existing, occurred_at)  # SLA met/missed uses when it REALLY happened
    db.flush()  # writes the UPDATE (version-checked) and bumps `version` exactly once for this transaction

    status_changed = old_status != existing.status
    logged, reason, attempt_outcome = False, None, None
    if status_changed:
        record_history_entry(
            db, delivery_id=existing.id, changed_by_user_id=actor.id, changed_by_display_name=agent_name,
            old_status=old_status, new_status=existing.status, changed_at=occurred_at,
            note="Updated via offline sync" if not stale else "Updated via offline sync (reconciled with newer changes)",
            commit=False,
        )
        logged, reason, attempt_outcome = _log_synced_attempt(
            db, existing, agent_id=attempt_agent_id, status=existing.status,
            is_partial=record_data.get("is_partial", False), reason_code_id=reason_code_id,
            notes=record_data.get("notes") or record_data.get("partial_notes"),
            attempted_at=occurred_at,
        )

    outcome = OUTCOME_RECONCILED if stale else OUTCOME_APPLIED
    audit(
        "reconciled" if stale else "accepted",
        code=STALE_VERSION if stale else None,
        message=(f"Based on version {base_version}; the delivery was at version {version_before}, "
                 "and the change is still valid against its current state.") if stale else None,
        before=version_before, after=existing.version, status_before=status_before,
        status_after=existing.status, delivery=existing,
    )
    _record_event(db, event_id=event_id, actor=actor, delivery_id=existing.id, outcome=outcome)
    db.commit()  # <- the ONE commit for this record
    db.refresh(existing)

    follow_ups: List[FollowUp] = []
    if status_changed:
        new_status_value = _status_value(existing.status)
        follow_ups.append(FollowUp("customer_notification", lambda: notify_customer_of_status_change(
            db, delivery_id=existing.id, order_id=existing.order_id, new_status=new_status_value,
            customer_email=existing.customer_email, customer_phone=existing.customer_phone,
            customer_id=existing.customer_id,
        )))
        follow_ups.append(FollowUp("tracking_broadcast", lambda: broadcast_sync(
            tracking_room(existing.id), {"event": "status_changed", "status": new_status_value})))
        if existing.status == DeliveryStatus.delivered:
            # Same return/exchange completion hook as the online PATCH path: an agent completing a
            # return pickup while offline still needs to trigger the refund/exchange once it syncs.
            follow_ups.append(FollowUp("return_pickup_completion", lambda: handle_return_pickup_completion(db, existing)))
        if logged and attempt_outcome == "failed_attempt":
            follow_ups.append(FollowUp("rto_eligibility", lambda: _check_rto(db, existing, reason)))
    return SyncResolution(existing, None, outcome, follow_ups)


def resolve_and_apply(
    record_data: dict,
    db: Session,
    actor: UserDB,
    clock_offset: timedelta | None = None,
) -> tuple[DeliveryRecordDB, dict | None]:
    """
    Backwards-compatible wrapper: resolve one record and run its follow-ups, returning (record, conflict).
    New code should use services/sync_processor.process_sync_record(), which adds idempotency and reports
    an explicit outcome, replay flag and follow-up results per record.
    """
    resolution = resolve_record(record_data, db, actor, clock_offset)
    run_follow_ups(resolution.follow_ups)
    return resolution.record, resolution.conflict
