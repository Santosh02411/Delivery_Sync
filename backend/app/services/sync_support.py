"""
Pure helpers for the /sync pipeline (no database access, no web framework) — so the rules are unit-tested
directly (tests/test_sync_support.py). The database-touching orchestration is services/sync_processor.py.

THE CONTRACT (also documented in docs/SYNC_CONTRACT.md and asserted by tests/test_sync_contract.py)

  A batch is PARTIAL SUCCESS. Each record is processed independently: one invalid / forbidden / failing
  record never prevents, undoes, or delays the others, and the request still answers 200 with one explicit
  result per record. (An offline backlog is dozens of unrelated deliveries; blocking all of them because one
  is bad would be worse than the partial state.)

  Each record is ALL-OR-NOTHING: the delivery change, its history entry, the delivery attempt, the audit
  decision and the idempotency-ledger row are committed in ONE database transaction, or not at all. There is
  no state in which a delivery shows "delivered" but has no history / attempt / audit row.

  AFTER that commit, best-effort FOLLOW-UPS run (customer notification, live-tracking broadcast, return-pickup
  completion, RTO eligibility). A failure there never undoes the delivery change; it is reported in the
  record's `side_effects`. The two that change data (return-pickup completion, RTO check) are idempotent and
  are re-run if the same event is replayed.

  Every record is IDEMPOTENT per event: the same `event_id` from the same user is applied at most once;
  re-sending it returns the original outcome with `replayed: true` and the delivery's CURRENT state.

Outcomes, one per record, in request order:
  applied          the change was accepted and saved (or the delivery was created)
  reconciled       the change was based on an older version but is still valid; saved
  already_applied  nothing to do: this exact change had already been applied
  rejected         refused as a conflict (stale and superseded, forbidden transition); see `code`/`message`
  error            could not be considered at all (see `code`, and `retryable` to know whether to resend)
"""

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

OUTCOME_APPLIED = "applied"
OUTCOME_RECONCILED = "reconciled"
OUTCOME_ALREADY_APPLIED = "already_applied"
OUTCOME_REJECTED = "rejected"
OUTCOME_ERROR = "error"

# Error codes whose cause can disappear on its own, so the device should simply send the record again.
RETRYABLE_ERROR_CODES = {"concurrent_update", "pod_required", "server_error"}


def derive_event_id(actor_id: str, record: Dict[str, Any], local_revision: Optional[int]) -> Optional[str]:
    """
    A stable key for a record sent WITHOUT an `event_id` (an older client). Built from everything that makes
    this particular edit this edit — including the device's own timestamp and content — so two genuinely
    different edits can never collide, while a resend of the same edit always produces the same key.
    Returns None when there isn't enough to be sure (no local_revision): such a request simply isn't
    deduplicated, exactly as before.
    """
    if local_revision is None:
        return None
    parts = [
        str(actor_id), str(record.get("id")), str(local_revision), str(record.get("base_version")),
        str(getattr(record.get("status"), "value", record.get("status"))),
        str(record.get("updated_at")), str(record.get("notes")), str(record.get("location_note")),
        str(record.get("is_partial")), str(record.get("partial_notes")), str(record.get("reason_code_id")),
    ]
    return "derived:" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:40]


@dataclass
class FollowUp:
    """A best-effort action that runs AFTER the record's transaction has committed."""
    name: str
    run: Callable[[], None]


def run_follow_ups(follow_ups: List[FollowUp]) -> List[Dict[str, str]]:
    """
    Runs each follow-up in isolation. A failure is logged and reported ({"name", "status": "failed"}); it never
    raises, never stops the others, and (because the delivery change is already committed) never undoes anything.
    """
    reports = []
    for follow_up in follow_ups:
        try:
            follow_up.run()
            reports.append({"name": follow_up.name, "status": "done"})
        except Exception:  # noqa: BLE001 - best effort by design
            logger.exception("Sync follow-up '%s' failed after the delivery change was saved.", follow_up.name)
            reports.append({"name": follow_up.name, "status": "failed"})
    return reports


def _has_class_named(exc: BaseException, name: str) -> bool:
    return any(cls.__name__ == name for cls in type(exc).__mro__)


def classify_exception(exc: BaseException) -> Dict[str, Any]:
    """
    Maps an exception raised while processing ONE record to its explicit per-record error:
    {"code", "message", "retryable", "audit"}. `audit` says whether the refusal should be written to the
    delivery change audit log (a permanent decision about this record) — retryable states are not, or
    every 15-second retry would add another row.
    """
    code = getattr(exc, "code", None)
    if _has_class_named(exc, "StaleDataError"):
        return {"code": "concurrent_update", "retryable": True, "audit": False,
                "message": "This delivery was changed by someone else at the same moment; it will be retried automatically."}
    if isinstance(exc, ValueError):
        code = code or "invalid_record"
        return {"code": code, "message": str(exc), "retryable": code in RETRYABLE_ERROR_CODES,
                "audit": code not in RETRYABLE_ERROR_CODES}
    return {"code": "server_error", "retryable": True, "audit": False,
            "message": "The server couldn't process this record right now; it will be retried."}


@dataclass
class RecordResult:
    """Everything the route needs to know about ONE processed record."""
    record_id: str
    event_id: Optional[str]
    outcome: str
    record: Any = None                    # the FINAL stored delivery (None for errors)
    conflict: Optional[dict] = None
    replayed: bool = False
    error: Optional[dict] = None          # {"code","message","retryable","audit","denied"} for outcome == "error"
    side_effects: List[Dict[str, str]] = field(default_factory=list)

    def as_response(self) -> dict:
        body = {
            "id": self.record_id, "event_id": self.event_id, "outcome": self.outcome, "replayed": self.replayed,
            "side_effects": self.side_effects,
        }
        if self.outcome == OUTCOME_ERROR and self.error:
            body.update(code=self.error["code"], message=self.error["message"], retryable=self.error["retryable"])
        elif self.outcome == OUTCOME_REJECTED and self.conflict:
            body.update(code=self.conflict.get("code"), message=self.conflict.get("message"))
        return body
