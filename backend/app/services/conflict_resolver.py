"""
Conflict resolution logic for syncing offline delivery records.

Strategy: last-write-wins based on `updated_at` timestamp.
See docs/TECHNICAL_ARCHITECTURE.md for the full reasoning and known
limitations of this approach.
"""

from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.models.delivery import DeliveryRecordDB, DeliveryStatus
from app.models.delivery_history import DeliveryHistoryDB
from app.models.failed_delivery_reason import FailedDeliveryReasonDB
from app.models.user import UserDB, UserRole
from app.services.history import record_history_entry
from app.services.delivery_attempts import record_delivery_attempt, ATTEMPT_OUTCOMES
from app.services.notifications import notify_customer_of_status_change
from app.services.returns_workflow import handle_return_pickup_completion
from app.services.websocket_manager import broadcast_sync, tracking_room
from app.models.organization import OrganizationDB
from app.services.pod import org_requires_any_pod, pod_exists_for_delivery
from app.services.sla import assign_sla, classify_on_completion


def _log_synced_attempt(db: Session, db_record: DeliveryRecordDB, agent_id: str, status, is_partial: bool, reason_code_id: str | None, notes: str | None, attempted_at: datetime) -> None:
    """
    Shared by both branches of resolve_and_apply() below (new record,
    and an incoming-wins update) — logs a delivery_attempts row for a
    real attempt outcome synced from offline, mirroring what
    update_delivery() does for the online path. See
    routes/sync.py's SyncRecordIn docstring for why reason_code_id
    isn't hard-enforced here the way it is online.
    """
    status_value = status.value if hasattr(status, "value") else status
    outcome = "partial_delivery" if (status_value == "delivered" and is_partial) else status_value
    if outcome not in ATTEMPT_OUTCOMES:
        return

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
    )


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

# Statuses an agent may set from the field. `pending` (unassigned pool)
# and `cancelled` are dispatcher/customer-side states. (An agent CAN move
# a delivery assigned to them FROM `pending` — ownership is checked first.)
AGENT_SETTABLE_STATUSES = {
    DeliveryStatus.picked_up,
    DeliveryStatus.out_for_delivery,
    DeliveryStatus.delivered,
    DeliveryStatus.failed_attempt,
}
# An agent can't move a delivery OUT of these (a dispatcher can).
AGENT_LOCKED_STATUSES = {DeliveryStatus.delivered, DeliveryStatus.cancelled}

# Offline devices can have wrong clocks; tolerate some skew, but never let
# a record claim to be from the future — last-write-wins would otherwise
# let one forged far-future timestamp beat every legitimate update.
MAX_CLOCK_SKEW = timedelta(minutes=15)


class SyncAuthorizationError(ValueError):
    """A record was refused for authorization/validation reasons. Subclasses
    ValueError so the per-record `except ValueError` in routes/sync.py
    keeps working; `code` is machine-readable for clients and logs."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


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


def _check_status_transition(actor: UserDB, old_status, new_status) -> None:
    """Agents may only make field-realistic moves. Offline queues can skip
    steps (picked_up -> delivered with nothing in between), so this is an
    allow-list of settable statuses + locked source states, not a strict
    state machine. Dispatchers/admins are not restricted."""
    if actor.role != UserRole.agent or old_status == new_status:
        return
    if old_status in AGENT_LOCKED_STATUSES:
        raise SyncAuthorizationError(
            f"A delivery that is '{getattr(old_status, 'value', old_status)}' can only be changed by a dispatcher.",
            "transition_not_allowed",
        )
    if new_status not in AGENT_SETTABLE_STATUSES:
        raise SyncAuthorizationError(
            f"Agents can't set a delivery to '{getattr(new_status, 'value', new_status)}'.",
            "transition_not_allowed",
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


def resolve_and_apply(record_data: dict, db: Session, actor: UserDB) -> tuple[DeliveryRecordDB, dict | None]:
    """
    Given an incoming record from a client's offline sync batch, decide
    whether to insert it, update the existing one, or discard it (because
    the server's existing version is newer).

    Returns a (record, conflict) tuple. `record` is the FINAL resolved
    record as stored in the database — the client should overwrite its
    local copy with this, in case the server's version won the conflict.
    `conflict` is None unless the incoming change was actually discarded
    in favor of a newer server-side change, in which case it's a dict
    describing what was overridden and by whom — see routes/sync.py,
    which surfaces this to the client so a discarded change is never
    silent (previously: the caller had no way to tell "my change was
    applied" apart from "my change was silently dropped").

    `actor` is the AUTHENTICATED caller (from the access token), never
    derived from the payload. Raises ValueError (SyncAuthorizationError for
    authorization failures) for any record that can't be applied — the
    caller (routes/sync.py) catches this per-record so one bad record in
    a batch can't take down the whole sync request.
    """
    # Normalize incoming timestamps immediately so everything downstream
    # (comparisons AND storage) is consistently naive UTC, and refuse
    # far-future timestamps (see MAX_CLOCK_SKEW).
    record_data["created_at"] = _clamp_future(_normalize_to_naive_utc(record_data["created_at"]))
    record_data["updated_at"] = _clamp_future(_normalize_to_naive_utc(record_data["updated_at"]))

    existing = db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.id == record_data["id"]
    ).first()

    # SECURITY: identity and tenant come from the authenticated `actor`,
    # never from the payload. Authorization happens before anything about
    # the existing record can influence (or be returned in) the response.
    _authorize_sync_record(db, actor, record_data, existing)

    agent_name = actor.display_name
    # org_id is NEVER trusted from the client — always the actor's own org.
    record_data["org_id"] = actor.org_id
    # Sync never reassigns: an existing delivery keeps its current agent.
    # For a new (dispatcher-created) record, the validated payload agent_id
    # is the assignee. History is attributed to the real actor either way.
    if existing is not None:
        record_data["agent_id"] = existing.agent_id
    attempt_agent_id = record_data["agent_id"] or actor.id

    # reason_code_id isn't a column on DeliveryRecordDB (it lives on the
    # delivery_attempts log instead) — pull it out before constructing/
    # updating the record with the rest of record_data.
    reason_code_id = record_data.pop("reason_code_id", None)

    # ENFORCEMENT (Phase 1 — Proof of Delivery): this is the OTHER place
    # (besides routes/deliveries.py's online PATCH) a delivery can end
    # up "delivered" — an agent completing a delivery while offline,
    # synced here later. Same rule, same org opt-in: if the org requires
    # POD and none has been captured for this delivery yet, the record
    # is rejected (ValueError -> routes/sync.py's per-record `errors`
    # list) rather than silently marked delivered. It stays "pending" on
    # the client and retries on the next sync — which succeeds once the
    # agent's queued POD capture (see frontend services/podOfflineQueue.js)
    # has synced first. This never blocks orgs that haven't opted into
    # any pod_require_* setting.
    if record_data.get("status") == DeliveryStatus.delivered:
        org = db.query(OrganizationDB).filter(OrganizationDB.id == actor.org_id).first()
        if org and org_requires_any_pod(org) and not pod_exists_for_delivery(db, record_data["id"], actor.org_id):
            raise ValueError(
                "Proof of delivery is required before this delivery can be marked as delivered. "
                "It will sync automatically once proof of delivery has been captured."
            )

    if not existing:
        # No conflict — this is a new record, just insert it
        new_record = DeliveryRecordDB(**record_data)
        assign_sla(db, new_record)  # SLA (Phase 2): compute a deadline for this newly-synced delivery too
        if new_record.status == DeliveryStatus.delivered:
            classify_on_completion(new_record, new_record.updated_at)
        db.add(new_record)
        db.commit()
        db.refresh(new_record)

        record_history_entry(
            db,
            delivery_id=new_record.id,
            changed_by_user_id=actor.id,
            changed_by_display_name=agent_name,
            old_status=None,
            new_status=new_record.status,
            changed_at=new_record.created_at,
            note="Created via offline sync",
        )
        _log_synced_attempt(
            db, new_record, agent_id=attempt_agent_id, status=new_record.status,
            is_partial=record_data.get("is_partial", False), reason_code_id=reason_code_id,
            notes=record_data.get("notes") or record_data.get("partial_notes"),
            attempted_at=new_record.created_at,
        )
        return new_record, None

    # Conflict case: record already exists on the server.
    # Compare timestamps — whichever is later wins. Both sides are now
    # guaranteed naive UTC, so this comparison is safe.
    incoming_updated_at = record_data["updated_at"]
    if incoming_updated_at > existing.updated_at:
        # Incoming (client) change is newer — apply it, but only if the
        # actor is allowed to make this particular status change.
        _check_status_transition(actor, existing.status, record_data["status"])
        old_status = existing.status
        existing.status = record_data["status"]
        existing.notes = record_data.get("notes")
        existing.location_note = record_data.get("location_note")
        existing.updated_at = incoming_updated_at
        if record_data.get("proof_of_delivery") is not None:
            existing.proof_of_delivery = record_data.get("proof_of_delivery")
        if existing.status == DeliveryStatus.delivered:
            existing.is_partial = record_data.get("is_partial", False)
            existing.partial_notes = record_data.get("partial_notes") if existing.is_partial else None
            classify_on_completion(existing, incoming_updated_at)
        db.commit()
        db.refresh(existing)

        if old_status != existing.status:
            record_history_entry(
                db,
                delivery_id=existing.id,
                changed_by_user_id=actor.id,
                changed_by_display_name=agent_name,
                old_status=old_status,
                new_status=existing.status,
                changed_at=incoming_updated_at,
                note="Updated via offline sync",
            )
            notify_customer_of_status_change(
                db,
                delivery_id=existing.id,
                order_id=existing.order_id,
                new_status=existing.status.value if hasattr(existing.status, "value") else existing.status,
                customer_email=existing.customer_email,
                customer_phone=existing.customer_phone,
                customer_id=existing.customer_id,
            )
            broadcast_sync(tracking_room(existing.id), {
                "event": "status_changed",
                "status": existing.status.value if hasattr(existing.status, "value") else existing.status,
            })
            if existing.status == DeliveryStatus.delivered:
                # Same return/exchange completion hook as the online PATCH
                # path (routes/deliveries.py) — an agent completing a
                # return pickup while offline still needs to trigger the
                # refund/exchange once it syncs back.
                handle_return_pickup_completion(db, existing)
            _log_synced_attempt(
                db, existing, agent_id=attempt_agent_id, status=existing.status,
                is_partial=record_data.get("is_partial", False), reason_code_id=reason_code_id,
                notes=record_data.get("notes") or record_data.get("partial_notes"),
                attempted_at=incoming_updated_at,
            )
        return existing, None
    else:
        # Server's existing version is newer or equal — keep it, discard
        # incoming. This is the exact case that used to be silent: the
        # client's change is thrown away with no signal anywhere. Now we
        # build a conflict record describing what got overridden and (if
        # we can tell from the history log) who made the winning change,
        # so routes/sync.py can hand this back to the client to surface.
        winning_entry = (
            db.query(DeliveryHistoryDB)
            .filter(DeliveryHistoryDB.delivery_id == existing.id)
            .order_by(DeliveryHistoryDB.changed_at.desc())
            .first()
        )
        conflict = {
            "id": existing.id,
            "order_id": existing.order_id,
            "your_status": record_data["status"].value if hasattr(record_data["status"], "value") else record_data["status"],
            "your_updated_at": incoming_updated_at.isoformat(),
            "kept_status": existing.status.value if hasattr(existing.status, "value") else existing.status,
            "kept_updated_at": existing.updated_at.isoformat(),
            "kept_by": winning_entry.changed_by_display_name if winning_entry else None,
        }
        return existing, conflict

