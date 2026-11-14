"""
Processes ONE record of a POST /sync batch end to end: idempotency, the atomic decision, follow-ups, and
conversion of every failure into an explicit per-record result. The contract this implements is written
down in services/sync_support.py (and docs/SYNC_CONTRACT.md); the rules for WHICH change wins live in
services/conflict_resolver.py.

Nothing raised while processing a record escapes this function: a record that fails — for any reason,
including a bug — becomes an `error` result for that record only, after its transaction has been rolled
back, and the rest of the batch carries on.
"""

import json
import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.delivery import DeliveryRecordDB, DeliveryStatus
from app.models.sync_event import SyncEventDB
from app.models.user import UserDB
from app.services.conflict_resolver import SyncAuthorizationError, _authorize_sync_record, resolve_record
from app.services.returns_workflow import handle_return_pickup_completion
from app.services.sync_support import (
    FollowUp, RecordResult, classify_exception, run_follow_ups,
    OUTCOME_ERROR, OUTCOME_APPLIED, OUTCOME_RECONCILED, OUTCOME_REJECTED,
)

logger = logging.getLogger(__name__)


def _find_event(db: Session, actor_id: str, event_id: str) -> Optional[SyncEventDB]:
    return db.query(SyncEventDB).filter(
        SyncEventDB.actor_user_id == actor_id, SyncEventDB.event_id == event_id
    ).first()


def _is_integrity_error(exc: BaseException) -> bool:
    return any(cls.__name__ == "IntegrityError" for cls in type(exc).__mro__)


def _replay(db: Session, actor: UserDB, record_data: dict, ledger: SyncEventDB) -> RecordResult:
    """
    This exact event was already decided: do NOT apply it again. Answer with the original outcome and the
    delivery's CURRENT state (the device overwrites its local copy with it, so it converges even if the
    original response was lost). The caller must still be allowed to see the delivery now — access can have
    changed since — so authorization is re-checked.
    """
    delivery = db.query(DeliveryRecordDB).filter(DeliveryRecordDB.id == ledger.delivery_id).first()
    if delivery is None:
        raise SyncAuthorizationError("Delivery not found.", "not_found")
    _authorize_sync_record(db, actor, record_data, delivery)

    conflict = None
    if ledger.outcome == OUTCOME_REJECTED and ledger.result_json:
        conflict = json.loads(ledger.result_json).get("conflict")

    follow_ups = []
    if ledger.outcome in (OUTCOME_APPLIED, OUTCOME_RECONCILED) and delivery.status == DeliveryStatus.delivered:
        # The one follow-up that is safe — and valuable — to repeat: if the first request committed the change and
        # then died before the refund/exchange step finished, the replay completes it (it is idempotent: it does
        # nothing once the return has been handled).
        follow_ups.append(FollowUp("return_pickup_completion", lambda: handle_return_pickup_completion(db, delivery)))

    return RecordResult(
        record_id=record_data["id"], event_id=ledger.event_id, outcome=ledger.outcome, record=delivery,
        conflict=conflict, replayed=True, side_effects=run_follow_ups(follow_ups),
    )


def process_sync_record(
    record_data: dict,
    db: Session,
    actor: UserDB,
    clock_offset: timedelta | None = None,
    event_id: str | None = None,
) -> RecordResult:
    record_id = record_data["id"]
    try:
        if event_id:
            ledger = _find_event(db, actor.id, event_id)
            if ledger is not None:
                return _replay(db, actor, record_data, ledger)

        resolution = resolve_record(record_data, db, actor, clock_offset, event_id)
        return RecordResult(
            record_id=record_id, event_id=event_id, outcome=resolution.outcome,
            record=resolution.record, conflict=resolution.conflict,
            side_effects=run_follow_ups(resolution.follow_ups),
        )
    except Exception as exc:  # noqa: BLE001 - nothing may escape: one record must never sink the batch
        db.rollback()  # discard everything this record wrote; earlier records were already committed on their own

        if event_id and _is_integrity_error(exc):
            # Two requests carrying the SAME event raced; the database's unique (actor, event) constraint let only
            # one of them in. We are the loser: nothing of ours was kept, so answer from the winner's decision.
            try:
                ledger = _find_event(db, actor.id, event_id)
                if ledger is not None:
                    return _replay(db, actor, record_data, ledger)
            except Exception as replay_exc:  # noqa: BLE001
                db.rollback()
                exc = replay_exc

        info = classify_exception(exc)
        info["denied"] = isinstance(exc, SyncAuthorizationError)
        if info["code"] == "server_error":
            logger.exception("Unexpected error processing sync record %s", record_id)
        return RecordResult(record_id=record_id, event_id=event_id, outcome=OUTCOME_ERROR, error=info)
