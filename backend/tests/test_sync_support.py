"""Unit tests for the pure rules behind the /sync contract (services/sync_support.py) — no database needed."""

from datetime import datetime

from app.services.sync_support import (
    FollowUp, RecordResult, RETRYABLE_ERROR_CODES, classify_exception, derive_event_id, run_follow_ups,
    OUTCOME_APPLIED, OUTCOME_ERROR, OUTCOME_REJECTED,
)


def record(**overrides):
    base = {"id": "d1", "status": "delivered", "base_version": 1, "updated_at": datetime(2026, 1, 1, 10, 0, 0),
            "notes": None, "location_note": None, "is_partial": False, "partial_notes": None, "reason_code_id": None}
    base.update(overrides)
    return base


# ---------- derived idempotency keys (older clients that send no event_id) ----------

def test_no_derived_key_without_a_local_revision():
    assert derive_event_id("u1", record(), None) is None       # not enough to be sure: simply not deduplicated


def test_the_same_edit_always_derives_the_same_key():
    assert derive_event_id("u1", record(), 3) == derive_event_id("u1", record(), 3)


def test_a_different_edit_never_collides_even_with_the_same_revision_number():
    """local_revision restarts if a device's storage is wiped; the key must still tell two edits apart."""
    base = derive_event_id("u1", record(), 1)
    assert derive_event_id("u2", record(), 1) != base                                    # another user
    assert derive_event_id("u1", record(id="d2"), 1) != base                             # another delivery
    assert derive_event_id("u1", record(status="failed_attempt"), 1) != base             # another change
    assert derive_event_id("u1", record(updated_at=datetime(2026, 1, 1, 10, 5)), 1) != base   # another moment
    assert derive_event_id("u1", record(notes="left with neighbour"), 1) != base         # other content
    assert derive_event_id("u1", record(base_version=2), 1) != base                      # based on another version
    assert derive_event_id("u1", record(), 2) != base                                    # another revision


def test_derived_keys_have_the_shape_the_ledger_accepts():
    key = derive_event_id("u1", record(), 1)
    assert key.startswith("derived:") and len(key) <= 128


def test_enum_and_plain_status_values_derive_the_same_key():
    class FakeEnum:
        value = "delivered"
    assert derive_event_id("u1", record(status=FakeEnum()), 1) == derive_event_id("u1", record(status="delivered"), 1)


# ---------- follow-ups run in isolation ----------

def test_a_failing_follow_up_is_reported_and_never_stops_the_others():
    ran = []

    def boom():
        raise RuntimeError("SMTP is down")

    reports = run_follow_ups([
        FollowUp("customer_notification", boom),
        FollowUp("tracking_broadcast", lambda: ran.append("broadcast")),
        FollowUp("return_pickup_completion", lambda: ran.append("return")),
    ])
    assert ran == ["broadcast", "return"]
    assert reports == [
        {"name": "customer_notification", "status": "failed"},
        {"name": "tracking_broadcast", "status": "done"},
        {"name": "return_pickup_completion", "status": "done"},
    ]


def test_no_follow_ups_means_no_reports():
    assert run_follow_ups([]) == []


# ---------- exception -> explicit per-record error ----------

class StaleDataError(Exception):       # same NAME as sqlalchemy.orm.exc.StaleDataError (matched by name, no import needed)
    pass


class CodedRefusal(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def test_a_concurrent_update_is_retryable_and_not_audited():
    info = classify_exception(StaleDataError("version mismatch"))
    assert (info["code"], info["retryable"], info["audit"]) == ("concurrent_update", True, False)


def test_waiting_for_proof_of_delivery_is_retryable_and_not_audited():
    """Retried every 15 seconds: auditing each attempt would flood the audit log."""
    info = classify_exception(CodedRefusal("POD needed", "pod_required"))
    assert (info["code"], info["retryable"], info["audit"]) == ("pod_required", True, False)


def test_permanent_refusals_are_not_retryable_and_are_audited():
    for code in ("not_assigned", "identity_mismatch", "not_found", "invalid_agent", "base_version_required"):
        info = classify_exception(CodedRefusal("nope", code))
        assert (info["code"], info["retryable"], info["audit"]) == (code, False, True)


def test_a_plain_value_error_is_an_invalid_record():
    info = classify_exception(ValueError("bad value"))
    assert (info["code"], info["retryable"], info["audit"], info["message"]) == ("invalid_record", False, True, "bad value")


def test_an_unexpected_exception_becomes_a_retryable_server_error_without_leaking_details():
    info = classify_exception(RuntimeError("connection to db-internal-7:5432 refused"))
    assert (info["code"], info["retryable"], info["audit"]) == ("server_error", True, False)
    assert "db-internal" not in info["message"]


def test_retryable_codes_match_what_the_clients_treat_as_retryable():
    assert RETRYABLE_ERROR_CODES == {"concurrent_update", "pod_required", "server_error"}


# ---------- the response shape ----------

def test_result_shapes():
    ok = RecordResult("d1", "evt-12345678", OUTCOME_APPLIED, side_effects=[{"name": "x", "status": "done"}])
    assert ok.as_response() == {"id": "d1", "event_id": "evt-12345678", "outcome": "applied", "replayed": False,
                                "side_effects": [{"name": "x", "status": "done"}]}

    rejected = RecordResult("d1", None, OUTCOME_REJECTED, conflict={"code": "stale_version", "message": "Cancelled."})
    assert rejected.as_response()["code"] == "stale_version" and rejected.as_response()["message"] == "Cancelled."

    err = RecordResult("d1", None, OUTCOME_ERROR, error={"code": "not_assigned", "message": "No.", "retryable": False})
    body = err.as_response()
    assert (body["outcome"], body["code"], body["retryable"]) == ("error", "not_assigned", False)

    replay = RecordResult("d1", "evt-12345678", OUTCOME_APPLIED, replayed=True)
    assert replay.as_response()["replayed"] is True
