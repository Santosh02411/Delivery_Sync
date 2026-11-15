"""
The /sync CONTRACT, asserted end to end (see services/sync_support.py and docs/SYNC_CONTRACT.md):

  * a batch is PARTIAL SUCCESS with one explicit, ordered result per record
  * each record is ALL-OR-NOTHING: delivery change + history + attempt + audit + idempotency row, or none
  * each record is IDEMPOTENT per event_id: a resend applies nothing twice and says so (`replayed`)
  * best-effort follow-ups (notifications...) can fail without undoing the saved change
  * a refusal that is not a decision about the change (not yours, busy, ...) is NOT frozen: a retry is evaluated afresh
"""

import uuid
from datetime import datetime, timedelta


def _signup_agent(client, invite_code, username):
    resp = client.post("/auth/signup", json={
        "username": username, "email": f"{username}@example.com", "password": "correct-horse-battery",
        "role": "agent", "display_name": username.replace("_", " ").title(), "invite_code": invite_code,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["user"]["id"], {"Authorization": f"Bearer {body['access_token']}"}


def _create_delivery(client, auth_headers, agent_id, status="pending"):
    delivery_id = str(uuid.uuid4())
    created = datetime.utcnow()
    resp = client.post("/deliveries/", json={
        "id": delivery_id, "agent_id": agent_id, "order_id": f"ORD-{uuid.uuid4().hex[:8]}", "status": status,
        "created_at": created.isoformat(), "updated_at": created.isoformat(),
    }, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _record(delivery, status, base_version=None, minutes=1, **extra):
    now = datetime.utcnow()
    return {
        "id": delivery["id"], "agent_id": delivery["agent_id"], "order_id": delivery["order_id"], "status": status,
        "created_at": now.isoformat(), "updated_at": (now + timedelta(minutes=minutes)).isoformat(),
        "base_version": delivery["version"] if base_version is None else base_version, **extra,
    }


def _sync(client, records, headers):
    resp = client.post("/sync", json={"records": records}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _state(client, auth_headers, delivery_id):
    """Everything this contract says is written together, read back through the API."""
    delivery = client.get(f"/deliveries/{delivery_id}", headers=auth_headers).json()
    return {
        "status": delivery["status"], "version": delivery["version"],
        "history": [h["new_status"] for h in client.get(f"/deliveries/{delivery_id}/history", headers=auth_headers).json()],
        "attempts": len(client.get(f"/deliveries/{delivery_id}/attempts", headers=auth_headers).json()),
        "audit": [(a["outcome"], a["code"]) for a in client.get(f"/deliveries/{delivery_id}/change-audit", headers=auth_headers).json()],
    }


def _setup(client, signed_up_admin, auth_headers, name):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], name)
    return agent_id, agent_headers


# --------------------------------------------------------------------------------------
# Partial success with explicit, ordered per-record results
# --------------------------------------------------------------------------------------

def test_a_batch_is_partial_success_with_one_explicit_result_per_record_in_order(client, signed_up_admin, auth_headers):
    code = signed_up_admin["org_invite_code"]
    mine_id, mine_headers = _signup_agent(client, code, "contract_mine")
    other_id, _ = _signup_agent(client, code, "contract_other")
    first = _create_delivery(client, auth_headers, mine_id)
    foreign = _create_delivery(client, auth_headers, other_id)          # someone else's delivery
    third = _create_delivery(client, auth_headers, mine_id)

    body = _sync(client, [
        _record(first, "out_for_delivery", event_id="evt-first-0001"),
        {**_record(foreign, "out_for_delivery", event_id="evt-forbidden-01"), "agent_id": mine_id},
        _record(third, "out_for_delivery", event_id="evt-third-0001"),
    ], mine_headers)

    results = body["results"]
    assert [r["id"] for r in results] == [first["id"], foreign["id"], third["id"]]           # request order
    assert [r["outcome"] for r in results] == ["applied", "error", "applied"]
    assert results[1]["code"] == "not_assigned" and results[1]["retryable"] is False
    assert [r["event_id"] for r in results] == ["evt-first-0001", "evt-forbidden-01", "evt-third-0001"]

    # the classic fields stay consistent with it (existing clients read only these)
    assert [r["id"] for r in body["resolved_records"]] == [first["id"], third["id"]]
    assert [e["code"] for e in body["errors"]] == ["not_assigned"]

    # ...and the bad record neither blocked nor undid its neighbours
    assert _state(client, auth_headers, first["id"])["status"] == "out_for_delivery"
    assert _state(client, auth_headers, third["id"])["status"] == "out_for_delivery"
    assert _state(client, auth_headers, foreign["id"])["status"] == "pending"


def test_outcomes_distinguish_applied_reconciled_already_applied_and_rejected(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _setup(client, signed_up_admin, auth_headers, "contract_outcomes")

    applied = _create_delivery(client, auth_headers, agent_id)
    assert _sync(client, [_record(applied, "out_for_delivery")], agent_headers)["results"][0]["outcome"] == "applied"

    # based on an OLD version, but still valid against the current state -> reconciled
    reconciled = _create_delivery(client, auth_headers, agent_id)
    assert client.patch(f"/deliveries/{reconciled['id']}/priority", json={"priority": "urgent"}, headers=auth_headers).status_code == 200
    assert _sync(client, [_record(reconciled, "out_for_delivery", base_version=reconciled["version"])], agent_headers)["results"][0]["outcome"] == "reconciled"

    # an exact re-send of a final change (no event_id) -> already_applied
    done = _create_delivery(client, auth_headers, agent_id)
    record = _record(done, "delivered")
    _sync(client, [record], agent_headers)
    assert _sync(client, [record], agent_headers)["results"][0]["outcome"] == "already_applied"

    # an offline agent can't revive what a dispatcher cancelled -> rejected, with the structured conflict
    cancelled = _create_delivery(client, auth_headers, agent_id)
    client.patch(f"/deliveries/{cancelled['id']}", json={"status": "cancelled", "updated_at": datetime.utcnow().isoformat(), "base_version": cancelled["version"]}, headers=auth_headers)
    rejected = _sync(client, [_record(cancelled, "delivered", base_version=cancelled["version"])], agent_headers)
    assert rejected["results"][0]["outcome"] == "rejected" and rejected["results"][0]["code"]
    assert len(rejected["conflicts"]) == 1


def test_event_id_is_validated(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _setup(client, signed_up_admin, auth_headers, "contract_validation")
    delivery = _create_delivery(client, auth_headers, agent_id)
    for bad in ("short", "has spaces in it", "x" * 200, "semi;colon-in-it"):
        resp = client.post("/sync", json={"records": [_record(delivery, "out_for_delivery", event_id=bad)]}, headers=agent_headers)
        assert resp.status_code == 422, bad


# --------------------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------------------

def test_resending_the_same_event_applies_nothing_twice_and_says_so(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _setup(client, signed_up_admin, auth_headers, "contract_idem")
    delivery = _create_delivery(client, auth_headers, agent_id)
    record = _record(delivery, "delivered", event_id="evt-deliver-0001")

    first = _sync(client, [record], agent_headers)
    after_first = _state(client, auth_headers, delivery["id"])
    replay = _sync(client, [record], agent_headers)           # the response was lost; the device sends the SAME event again

    assert first["results"][0]["outcome"] == "applied" and first["results"][0]["replayed"] is False
    assert replay["results"][0]["outcome"] == "applied" and replay["results"][0]["replayed"] is True   # the ORIGINAL outcome
    assert replay["errors"] == [] and replay["conflicts"] == []
    assert replay["resolved_records"][0]["status"] == "delivered"
    assert _state(client, auth_headers, delivery["id"]) == after_first          # not one extra row, not one extra version
    assert after_first["attempts"] == 1 and after_first["audit"] == [("accepted", None)]   # (a no-event resend would add "reconciled")


def test_a_replay_returns_the_deliverys_current_state_not_a_stale_snapshot(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _setup(client, signed_up_admin, auth_headers, "contract_current")
    delivery = _create_delivery(client, auth_headers, agent_id)
    record = _record(delivery, "out_for_delivery", event_id="evt-current-0001")
    first = _sync(client, [record], agent_headers)

    assert client.patch(f"/deliveries/{delivery['id']}/priority", json={"priority": "urgent"}, headers=auth_headers).status_code == 200

    replay = _sync(client, [record], agent_headers)
    assert replay["results"][0]["replayed"] is True
    assert replay["resolved_records"][0]["version"] > first["resolved_records"][0]["version"]   # the device converges on the truth
    assert replay["resolved_records"][0]["priority"] == "urgent"


def test_a_refused_conflict_is_remembered_too(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _setup(client, signed_up_admin, auth_headers, "contract_conflict")
    delivery = _create_delivery(client, auth_headers, agent_id)
    client.patch(f"/deliveries/{delivery['id']}", json={"status": "cancelled", "updated_at": datetime.utcnow().isoformat(), "base_version": delivery["version"]}, headers=auth_headers)
    record = _record(delivery, "delivered", base_version=delivery["version"], event_id="evt-conflict-001")

    first = _sync(client, [record], agent_headers)
    replay = _sync(client, [record], agent_headers)

    assert first["results"][0]["outcome"] == "rejected" and replay["results"][0]["outcome"] == "rejected"
    assert replay["results"][0]["replayed"] is True
    assert replay["conflicts"][0]["code"] == first["conflicts"][0]["code"]        # the same, structured answer
    assert [a for a in _state(client, auth_headers, delivery["id"])["audit"] if a[0] == "rejected"] == [("rejected", first["conflicts"][0]["code"])]   # decided once


def test_a_new_edit_is_a_new_event_even_if_it_looks_similar(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _setup(client, signed_up_admin, auth_headers, "contract_new_edit")
    delivery = _create_delivery(client, auth_headers, agent_id)
    _sync(client, [_record(delivery, "out_for_delivery", event_id="evt-edit-000001")], agent_headers)
    version = client.get(f"/deliveries/{delivery['id']}", headers=auth_headers).json()["version"]
    second = _sync(client, [_record(delivery, "failed_attempt", base_version=version, event_id="evt-edit-000002")], agent_headers)
    assert second["results"][0]["outcome"] == "applied" and second["results"][0]["replayed"] is False
    assert _state(client, auth_headers, delivery["id"])["status"] == "failed_attempt"


def test_older_clients_without_event_id_are_still_deduplicated_when_they_send_a_local_revision(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _setup(client, signed_up_admin, auth_headers, "contract_derived")
    delivery = _create_delivery(client, auth_headers, agent_id)
    record = _record(delivery, "delivered", local_revision=4)
    first = _sync(client, [record], agent_headers)
    replay = _sync(client, [record], agent_headers)
    assert first["results"][0]["replayed"] is False and replay["results"][0]["replayed"] is True
    assert first["results"][0]["event_id"].startswith("derived:")
    assert _state(client, auth_headers, delivery["id"])["attempts"] == 1


def test_a_refusal_that_is_not_a_decision_about_the_change_is_not_frozen(client, signed_up_admin, auth_headers):
    """'Not assigned to you' depends on the world: once the dispatcher assigns the delivery, the SAME event must work."""
    code = signed_up_admin["org_invite_code"]
    owner_id, _ = _signup_agent(client, code, "contract_owner")
    late_id, late_headers = _signup_agent(client, code, "contract_late")
    delivery = _create_delivery(client, auth_headers, owner_id)
    record = {**_record(delivery, "out_for_delivery", event_id="evt-reassign-001"), "agent_id": late_id}

    refused = _sync(client, [record], late_headers)
    assert refused["results"][0]["outcome"] == "error" and refused["results"][0]["code"] == "not_assigned"

    resp = client.patch(f"/deliveries/{delivery['id']}/assign-agent", json={"agent_id": late_id}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    current = client.get(f"/deliveries/{delivery['id']}", headers=auth_headers).json()
    retried = _sync(client, [{**record, "base_version": current["version"]}], late_headers)

    assert retried["results"][0]["outcome"] == "applied" and retried["results"][0]["replayed"] is False


def test_a_race_between_two_requests_with_the_same_event_resolves_to_a_replay(client, signed_up_admin, auth_headers, monkeypatch):
    """Both requests pass the 'seen it before?' check; the database's unique (user, event) constraint lets only one in."""
    from app.services import sync_processor

    agent_id, agent_headers = _setup(client, signed_up_admin, auth_headers, "contract_race")
    delivery = _create_delivery(client, auth_headers, agent_id)
    record = _record(delivery, "delivered", event_id="evt-race-000001")
    _sync(client, [record], agent_headers)
    after_first = _state(client, auth_headers, delivery["id"])

    real_find = sync_processor._find_event
    calls = {"n": 0}

    def blind_the_first_time(db, actor_id, event_id):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_find(db, actor_id, event_id)

    monkeypatch.setattr(sync_processor, "_find_event", blind_the_first_time)
    raced = _sync(client, [record], agent_headers)

    assert raced["errors"] == []
    assert raced["results"][0]["outcome"] == "applied" and raced["results"][0]["replayed"] is True
    assert _state(client, auth_headers, delivery["id"]) == after_first            # the loser wrote NOTHING


# --------------------------------------------------------------------------------------
# All-or-nothing per record
# --------------------------------------------------------------------------------------

def test_a_failure_in_the_middle_of_a_record_leaves_nothing_behind_and_does_not_sink_the_batch(client, signed_up_admin, auth_headers, monkeypatch):
    """The attempt log blows up AFTER the delivery was updated and the history entry added. Everything must be undone."""
    from app.services import conflict_resolver

    agent_id, agent_headers = _setup(client, signed_up_admin, auth_headers, "contract_atomic")
    doomed = _create_delivery(client, auth_headers, agent_id)
    fine = _create_delivery(client, auth_headers, agent_id)
    before = _state(client, auth_headers, doomed["id"])
    real_attempt = conflict_resolver.record_delivery_attempt

    def explode(*args, **kwargs):
        raise RuntimeError("disk full while writing the attempt")

    monkeypatch.setattr(conflict_resolver, "record_delivery_attempt", explode)
    body = _sync(client, [
        _record(doomed, "delivered", event_id="evt-doomed-0001"),          # 'delivered' logs an attempt -> explodes
        _record(fine, "out_for_delivery", event_id="evt-fine-000001"),     # no attempt row -> unaffected
    ], agent_headers)

    assert [r["outcome"] for r in body["results"]] == ["error", "applied"]
    assert body["results"][0]["code"] == "server_error" and body["results"][0]["retryable"] is True
    assert "disk full" not in str(body)                                    # internals are not leaked to the client
    assert _state(client, auth_headers, doomed["id"]) == before            # status, version, history, attempts, audit: ALL unchanged
    assert _state(client, auth_headers, fine["id"])["status"] == "out_for_delivery"

    # And because nothing was recorded for the failed event, the SAME event succeeds once the fault is gone.
    monkeypatch.setattr(conflict_resolver, "record_delivery_attempt", real_attempt)
    retried = _sync(client, [_record(doomed, "delivered", event_id="evt-doomed-0001")], agent_headers)
    assert retried["results"][0]["outcome"] == "applied" and retried["results"][0]["replayed"] is False
    after = _state(client, auth_headers, doomed["id"])
    assert after["status"] == "delivered" and after["attempts"] == 1 and after["history"][-1] == "delivered"
    assert after["audit"] == [("accepted", None)]


def test_one_successful_commit_means_exactly_one_version_bump(client, signed_up_admin, auth_headers):
    """It used to be two: one commit for the status, another when the attempt count was saved."""
    agent_id, agent_headers = _setup(client, signed_up_admin, auth_headers, "contract_version")
    delivery = _create_delivery(client, auth_headers, agent_id)
    body = _sync(client, [_record(delivery, "delivered")], agent_headers)
    assert body["resolved_records"][0]["version"] == delivery["version"] + 1


# --------------------------------------------------------------------------------------
# Best-effort follow-ups
# --------------------------------------------------------------------------------------

def test_a_failing_follow_up_never_undoes_the_saved_change_and_is_reported(client, signed_up_admin, auth_headers, monkeypatch):
    from app.services import conflict_resolver

    agent_id, agent_headers = _setup(client, signed_up_admin, auth_headers, "contract_followup")
    delivery = _create_delivery(client, auth_headers, agent_id)

    def smtp_down(*args, **kwargs):
        raise RuntimeError("SMTP is down")

    monkeypatch.setattr(conflict_resolver, "notify_customer_of_status_change", smtp_down)
    body = _sync(client, [_record(delivery, "out_for_delivery", event_id="evt-notify-0001")], agent_headers)

    result = body["results"][0]
    assert result["outcome"] == "applied" and body["errors"] == []              # NOT a failed record
    assert {"name": "customer_notification", "status": "failed"} in result["side_effects"]
    assert {"name": "tracking_broadcast", "status": "done"} in result["side_effects"]
    state = _state(client, auth_headers, delivery["id"])
    assert state["status"] == "out_for_delivery" and state["history"][-1] == "out_for_delivery"
    assert state["audit"] == [("accepted", None)]


def test_successful_follow_ups_are_listed(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _setup(client, signed_up_admin, auth_headers, "contract_listed")
    delivery = _create_delivery(client, auth_headers, agent_id)
    result = _sync(client, [_record(delivery, "delivered")], agent_headers)["results"][0]
    names = {s["name"]: s["status"] for s in result["side_effects"]}
    assert names["customer_notification"] == "done" and names["tracking_broadcast"] == "done" and names["return_pickup_completion"] == "done"
