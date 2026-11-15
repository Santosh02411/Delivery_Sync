"""
Version-based conflict resolution, the central transition policy, the change audit trail, and
real-time capture for deliveries.

The point of all of it: WHICH CHANGE WINS is decided by the server-managed `version` (what the client
last read) and the transition policy — never by comparing timestamps. Timestamps only say when
things happened.
"""

import uuid
from datetime import datetime, timedelta

CLOCK_TOLERANCE_SECONDS = 60


def _signup_agent(client, invite_code, username):
    resp = client.post("/auth/signup", json={
        "username": username, "email": f"{username}@example.com", "password": "correct-horse-battery",
        "role": "agent", "display_name": username.replace("_", " ").title(), "invite_code": invite_code,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["user"]["id"], {"Authorization": f"Bearer {body['access_token']}"}


def _create_delivery(client, auth_headers, agent_id, created_at=None, status="pending"):
    delivery_id = str(uuid.uuid4())
    created = created_at or datetime.utcnow()
    resp = client.post("/deliveries/", json={
        "id": delivery_id, "agent_id": agent_id, "order_id": f"ORD-{uuid.uuid4().hex[:8]}", "status": status,
        "created_at": created.isoformat(), "updated_at": created.isoformat(),
    }, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _record(delivery, status, base_version, minutes=1, **extra):
    now = datetime.utcnow()
    return {
        "id": delivery["id"], "agent_id": delivery["agent_id"], "order_id": delivery["order_id"], "status": status,
        "created_at": now.isoformat(), "updated_at": (now + timedelta(minutes=minutes)).isoformat(),
        "base_version": base_version, **extra,
    }


def _sync(client, records, headers, client_sent_at=None):
    body = {"records": records}
    if client_sent_at is not None:
        body["client_sent_at"] = client_sent_at.isoformat() + "Z"
    return client.post("/sync", json=body, headers=headers)


def _patch(client, delivery_id, headers, status, base_version=None, **extra):
    payload = {"status": status, "updated_at": datetime.utcnow().isoformat(), **extra}
    if base_version is not None:
        payload["base_version"] = base_version
    return client.patch(f"/deliveries/{delivery_id}", json=payload, headers=headers)


def _version(client, auth_headers, delivery_id):
    """The delivery's current server-managed version (what an up-to-date device has read)."""
    return client.get(f"/deliveries/{delivery_id}", headers=auth_headers).json()["version"]


def _audit(client, auth_headers, delivery_id):
    resp = client.get(f"/deliveries/{delivery_id}/change-audit", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "")).replace(tzinfo=None)


# ---------- the version itself ----------

def test_new_deliveries_start_at_version_1_and_every_change_bumps_it(client, signed_up_admin, auth_headers):
    agent_id, _ = _signup_agent(client, signed_up_admin["org_invite_code"], "ver_basic_agent")
    delivery = _create_delivery(client, auth_headers, agent_id)
    assert delivery["version"] == 1

    # An online status change, a priority change and an assignment change each move it forward.
    v2 = _patch(client, delivery["id"], auth_headers, "picked_up").json()["version"]
    v3 = client.patch(f"/deliveries/{delivery['id']}/priority", json={"priority": "urgent"}, headers=auth_headers).json()["version"]
    assert v2 > 1 and v3 > v2

    # Reads show the same server-managed number.
    assert client.get(f"/deliveries/{delivery['id']}", headers=auth_headers).json()["version"] == v3


# ---------- the scenario that motivated all this ----------

def test_agent_offline_cannot_revive_a_delivery_the_dispatcher_cancelled(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "ver_cancel_agent")
    delivery = _create_delivery(client, auth_headers, agent_id)           # version 1; the agent reads it

    cancelled = _patch(client, delivery["id"], auth_headers, "cancelled", base_version=1)   # dispatcher cancels
    assert cancelled.status_code == 200, cancelled.text
    cancelled_version = _version(client, auth_headers, delivery["id"])
    assert cancelled_version > 1

    # The agent was offline, marks it delivered, and the device timestamp looks NEWER than the cancellation.
    body = _sync(client, [_record(delivery, "delivered", base_version=1, minutes=30)], agent_headers).json()

    assert body["errors"] == []
    conflict = body["conflicts"][0]
    assert conflict["code"] == "stale_version"
    assert "cancelled" in conflict["message"]
    assert (conflict["base_version"], conflict["current_version"]) == (1, cancelled_version)
    assert conflict["kept_status"] == "cancelled" and conflict["your_status"] == "delivered"
    assert body["resolved_records"][0]["status"] == "cancelled"     # the server's copy comes back
    assert client.get(f"/deliveries/{delivery['id']}", headers=auth_headers).json()["status"] == "cancelled"

    rejected = [a for a in _audit(client, auth_headers, delivery["id"]) if a["outcome"] == "rejected"]
    assert rejected and rejected[0]["code"] == "stale_version"
    assert (rejected[0]["base_version"], rejected[0]["version_before"], rejected[0]["version_after"]) == (1, cancelled_version, cancelled_version)
    assert (rejected[0]["status_before"], rejected[0]["status_after"]) == ("cancelled", "delivered")  # attempted: delivered


def test_a_stale_dispatcher_cannot_revive_it_either_but_an_up_to_date_one_can(client, signed_up_admin, auth_headers):
    agent_id, _ = _signup_agent(client, signed_up_admin["org_invite_code"], "ver_reinstate_agent")
    delivery = _create_delivery(client, auth_headers, agent_id)
    assert _patch(client, delivery["id"], auth_headers, "cancelled", base_version=1).status_code == 200
    cancelled_version = _version(client, auth_headers, delivery["id"])

    stale = _patch(client, delivery["id"], auth_headers, "out_for_delivery", base_version=1)   # read before the cancel
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "stale_version" and detail["current_version"] == cancelled_version
    assert detail["server_record"]["status"] == "cancelled"

    deliberate = _patch(client, delivery["id"], auth_headers, "out_for_delivery", base_version=cancelled_version)  # saw the cancel, chose to reinstate
    assert deliberate.status_code == 200 and deliberate.json()["status"] == "out_for_delivery"


# ---------- accepted / reconciled / rejected ----------

def test_change_based_on_the_current_version_is_accepted(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "ver_accept_agent")
    delivery = _create_delivery(client, auth_headers, agent_id)
    body = _sync(client, [_record(delivery, "out_for_delivery", base_version=1)], agent_headers).json()
    assert body["errors"] == [] and body["conflicts"] == []
    new_version = body["resolved_records"][0]["version"]
    assert new_version > 1
    entry = _audit(client, auth_headers, delivery["id"])[-1]
    assert (entry["outcome"], entry["source"], entry["code"]) == ("accepted", "offline_sync", None)
    assert (entry["base_version"], entry["version_before"], entry["version_after"]) == (1, 1, new_version)


def test_stale_change_that_is_still_valid_is_reconciled_not_lost(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "ver_reconcile_agent")
    delivery = _create_delivery(client, auth_headers, agent_id)
    # While the agent is offline the dispatcher only changes the priority (version 1 -> 2).
    assert client.patch(f"/deliveries/{delivery['id']}/priority", json={"priority": "urgent"}, headers=auth_headers).status_code == 200
    after_priority = _version(client, auth_headers, delivery["id"])
    assert after_priority > 1

    body = _sync(client, [_record(delivery, "out_for_delivery", base_version=1)], agent_headers).json()

    assert body["errors"] == [] and body["conflicts"] == []
    applied = body["resolved_records"][0]
    assert applied["status"] == "out_for_delivery" and applied["priority"] == "urgent"   # the dispatcher's edit survived
    assert applied["version"] > after_priority
    entry = _audit(client, auth_headers, delivery["id"])[-1]
    assert (entry["outcome"], entry["code"]) == ("reconciled", "stale_version")
    assert (entry["base_version"], entry["version_before"], entry["version_after"]) == (1, after_priority, applied["version"])


def test_a_resend_of_an_already_applied_change_is_a_harmless_noop(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "ver_replay_agent")
    delivery = _create_delivery(client, auth_headers, agent_id)
    record = _record(delivery, "delivered", base_version=1)

    first = _sync(client, [record], agent_headers).json()
    replay = _sync(client, [record], agent_headers).json()   # the response was lost, so the device sends it again

    assert first["errors"] == [] and replay["errors"] == [] and replay["conflicts"] == []
    assert replay["resolved_records"][0]["status"] == "delivered"
    assert replay["resolved_records"][0]["version"] == first["resolved_records"][0]["version"]  # nothing re-applied
    assert len(client.get(f"/deliveries/{delivery['id']}/attempts", headers=auth_headers).json()) == 1
    assert [a["outcome"] for a in _audit(client, auth_headers, delivery["id"])] == ["accepted", "reconciled"]
    assert _audit(client, auth_headers, delivery["id"])[-1]["code"] == "already_applied"


def test_base_version_is_required_for_an_existing_delivery(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "ver_required_agent")
    delivery = _create_delivery(client, auth_headers, agent_id)
    record = _record(delivery, "out_for_delivery", base_version=1)
    del record["base_version"]
    body = _sync(client, [record], agent_headers).json()
    assert body["errors"][0]["code"] == "base_version_required"
    assert body["resolved_records"] == []
    refused = [a for a in _audit(client, auth_headers, delivery["id"]) if a["outcome"] == "rejected"]
    assert refused and refused[0]["code"] == "base_version_required"


def test_authorization_refusals_are_audited_too(client, signed_up_admin, auth_headers):
    code = signed_up_admin["org_invite_code"]
    owner_id, _ = _signup_agent(client, code, "ver_owner_agent")
    other_id, other_headers = _signup_agent(client, code, "ver_other_agent")
    delivery = _create_delivery(client, auth_headers, owner_id)
    record = _record(delivery, "delivered", base_version=1)
    record["agent_id"] = other_id
    assert _sync(client, [record], other_headers).json()["errors"][0]["code"] == "not_assigned"
    assert [a["code"] for a in _audit(client, auth_headers, delivery["id"])] == ["not_assigned"]


# ---------- the online endpoint obeys the same rules ----------

def test_online_patch_applies_the_same_policy(client, signed_up_admin, auth_headers):
    code = signed_up_admin["org_invite_code"]
    agent_id, agent_headers = _signup_agent(client, code, "ver_online_agent")
    _, other_headers = _signup_agent(client, code, "ver_online_other")
    delivery = _create_delivery(client, auth_headers, agent_id)

    # agents can't cancel
    assert _patch(client, delivery["id"], agent_headers, "cancelled").status_code == 403
    # another agent can't touch it at all
    assert _patch(client, delivery["id"], other_headers, "delivered").status_code == 404
    # clients that don't send a base_version still work (the policy still applies)
    ok = _patch(client, delivery["id"], agent_headers, "out_for_delivery")
    assert ok.status_code == 200 and ok.json()["version"] > 1
    # ...and once delivered, an agent can't reopen it
    current = _version(client, auth_headers, delivery["id"])
    assert _patch(client, delivery["id"], agent_headers, "delivered", base_version=current).status_code == 200
    current = _version(client, auth_headers, delivery["id"])
    assert _patch(client, delivery["id"], agent_headers, "picked_up", base_version=current).status_code == 403


def test_online_stale_update_against_a_cancelled_delivery_is_a_structured_409(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "ver_online409_agent")
    delivery = _create_delivery(client, auth_headers, agent_id)
    assert _patch(client, delivery["id"], auth_headers, "cancelled", base_version=1).status_code == 200

    resp = _patch(client, delivery["id"], agent_headers, "delivered", base_version=1)
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "stale_version" and detail["server_record"]["status"] == "cancelled"
    assert [a["outcome"] for a in _audit(client, auth_headers, delivery["id"])][-1] == "rejected"


# ---------- audit trail access ----------

def test_audit_endpoints_are_scoped_and_role_protected(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "ver_audit_agent")
    delivery = _create_delivery(client, auth_headers, agent_id)
    assert _patch(client, delivery["id"], auth_headers, "cancelled", base_version=1).status_code == 200
    _sync(client, [_record(delivery, "delivered", base_version=1)], agent_headers)

    assert client.get(f"/deliveries/{delivery['id']}/change-audit", headers=agent_headers).status_code == 403
    assert client.get("/admin/delivery-change-audit", headers=agent_headers).status_code == 403

    listed = client.get("/admin/delivery-change-audit?outcome=rejected", headers=auth_headers).json()
    assert any(a["delivery_id"] == delivery["id"] and a["code"] == "stale_version" for a in listed)

    other = client.post("/auth/signup", json={
        "username": f"audit_other_{uuid.uuid4().hex[:6]}", "email": f"audit_other_{uuid.uuid4().hex[:6]}@example.com",
        "password": "correct-horse-battery", "role": "admin", "display_name": "Other Admin", "org_name": f"Org_{uuid.uuid4().hex[:6]}",
    }).json()
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    assert client.get("/admin/delivery-change-audit", headers=other_headers).json() == []
    assert client.get(f"/deliveries/{delivery['id']}/change-audit", headers=other_headers).json() == []


# ---------- real-time capture ----------

def test_event_time_is_corrected_when_the_device_clock_is_wrong(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "ver_clock_agent")
    real_now = datetime.utcnow()
    delivery = _create_delivery(client, auth_headers, agent_id, created_at=real_now - timedelta(hours=1))

    # The device clock is 3 hours BEHIND. The agent really delivered it 10 minutes ago, so the device
    # stamped it (now - 10 min - 3 h), and its clock read (now - 3 h) when the request was sent.
    skew = timedelta(hours=3)
    record = _record(delivery, "delivered", base_version=1)
    record["updated_at"] = (real_now - timedelta(minutes=10) - skew).isoformat()
    body = _sync(client, [record], agent_headers, client_sent_at=real_now - skew).json()
    assert body["errors"] == [] and body["conflicts"] == []

    expected = real_now - timedelta(minutes=10)
    history = client.get(f"/deliveries/{delivery['id']}/history", headers=auth_headers).json()
    changed_at = _parse(history[-1]["changed_at"])
    assert abs((changed_at - expected).total_seconds()) < 120          # the REAL time it happened
    assert history[-1]["recorded_at"] is not None                      # and when the server saved it
    assert abs((_parse(history[-1]["recorded_at"]) - datetime.utcnow()).total_seconds()) < 120

    entry = _audit(client, auth_headers, delivery["id"])[-1]
    assert abs(entry["clock_offset_seconds"] - skew.total_seconds()) < 120
    assert abs((_parse(entry["occurred_at"]) - expected).total_seconds()) < 120
    assert abs((_parse(entry["client_occurred_at"]) - (expected - skew)).total_seconds()) < 5   # the raw device time is kept
    assert abs((_parse(entry["recorded_at"]) - datetime.utcnow()).total_seconds()) < 120
    assert abs((_parse(body["resolved_records"][0]["updated_at"]) - expected).total_seconds()) < 120


def test_a_roughly_correct_device_clock_is_trusted_as_is(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "ver_okclock_agent")
    real_now = datetime.utcnow()
    delivery = _create_delivery(client, auth_headers, agent_id, created_at=real_now - timedelta(hours=1))
    record = _record(delivery, "out_for_delivery", base_version=1)
    event = real_now - timedelta(minutes=5)
    record["updated_at"] = event.isoformat()
    _sync(client, [record], agent_headers, client_sent_at=real_now - timedelta(seconds=20))
    entry = _audit(client, auth_headers, delivery["id"])[-1]
    assert entry["clock_offset_seconds"] is None
    assert abs((_parse(entry["occurred_at"]) - event).total_seconds()) < 2


def test_online_changes_are_stamped_with_the_servers_real_time(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "ver_online_time_agent")
    delivery = _create_delivery(client, auth_headers, agent_id, created_at=datetime.utcnow() - timedelta(hours=1))
    resp = client.patch(f"/deliveries/{delivery['id']}", json={
        "status": "out_for_delivery", "updated_at": "2020-01-01T00:00:00",   # a client clock that is years wrong
    }, headers=agent_headers)
    assert resp.status_code == 200, resp.text
    assert abs((_parse(resp.json()["updated_at"]) - datetime.utcnow()).total_seconds()) < 60
    history = client.get(f"/deliveries/{delivery['id']}/history", headers=auth_headers).json()
    assert abs((_parse(history[-1]["changed_at"]) - datetime.utcnow()).total_seconds()) < 60


def test_unknown_base_version_zero_is_treated_as_the_oldest_possible(client, signed_up_admin, auth_headers):
    """Offline edits saved before versions existed are sent with base_version 0: they may be applied if
    still valid against the current state, but can never revive a cancelled/completed delivery."""
    code = signed_up_admin["org_invite_code"]
    agent_id, agent_headers = _signup_agent(client, code, "ver_legacy_agent")

    valid = _create_delivery(client, auth_headers, agent_id)
    body = _sync(client, [_record(valid, "out_for_delivery", base_version=0)], agent_headers).json()
    assert body["errors"] == [] and body["conflicts"] == []
    assert body["resolved_records"][0]["status"] == "out_for_delivery"

    cancelled = _create_delivery(client, auth_headers, agent_id)
    assert _patch(client, cancelled["id"], auth_headers, "cancelled", base_version=1).status_code == 200
    body = _sync(client, [_record(cancelled, "delivered", base_version=0, minutes=60)], agent_headers).json()
    assert body["conflicts"][0]["code"] == "stale_version"
    assert body["resolved_records"][0]["status"] == "cancelled"
