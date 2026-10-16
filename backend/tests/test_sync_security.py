"""
Security tests for POST /sync (offline batch sync).

/sync used to be unauthenticated, trusting agent_id/org_id from the
payload. It now requires an access token and derives identity + org from
it. These are the NEGATIVE tests: forged identity, cross-tenant access,
other agents' deliveries, forbidden status changes, and batch isolation.
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


def _create_delivery(client, auth_headers, agent_id):
    delivery_id = str(uuid.uuid4())
    now = datetime.utcnow()
    resp = client.post("/deliveries/", json={
        "id": delivery_id, "agent_id": agent_id, "order_id": f"ORD-{uuid.uuid4().hex[:8]}",
        "status": "pending", "created_at": now.isoformat(), "updated_at": now.isoformat(),
    }, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return delivery_id


def _record(delivery_id, agent_id, status="out_for_delivery", minutes=1, **extra):
    now = datetime.utcnow()
    return {
        "id": delivery_id,
        "agent_id": agent_id,
        "order_id": f"ORD-{delivery_id[:6]}",
        "status": status,
        "created_at": now.isoformat(),
        "updated_at": (now + timedelta(minutes=minutes)).isoformat(),
        **extra,
    }


def _sync(client, records, headers=None):
    return client.post("/sync", json={"records": records}, headers=headers or {})


def _second_org_admin(client):
    name = f"admin_{uuid.uuid4().hex[:8]}"
    resp = client.post("/auth/signup", json={
        "username": name, "email": f"{name}@example.com", "password": "correct-horse-battery",
        "role": "admin", "display_name": "Other Admin", "org_name": f"Org_{uuid.uuid4().hex[:8]}",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["user"], {"Authorization": f"Bearer {body['access_token']}"}, body["org_invite_code"]


# ---------- authentication ----------

def test_sync_requires_token(client, signed_up_admin, auth_headers):
    agent_id, _ = _signup_agent(client, signed_up_admin["org_invite_code"], "sync_noauth_agent")
    delivery_id = _create_delivery(client, auth_headers, agent_id)
    resp = _sync(client, [_record(delivery_id, agent_id)])
    assert resp.status_code == 401


def test_sync_rejects_garbage_and_non_staff_tokens(client, signed_up_customer, customer_auth_headers):
    assert _sync(client, [], headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401
    # a valid CUSTOMER token is not a staff identity
    assert _sync(client, [], headers=customer_auth_headers).status_code == 401


def test_sync_rejects_deactivated_user(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "sync_deact_agent")
    delivery_id = _create_delivery(client, auth_headers, agent_id)
    resp = client.patch(f"/admin/users/{agent_id}/deactivate", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert _sync(client, [_record(delivery_id, agent_id)], agent_headers).status_code == 403


# ---------- happy path (agent updating their own delivery) ----------

def test_agent_can_sync_own_assigned_delivery(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "sync_ok_agent")
    delivery_id = _create_delivery(client, auth_headers, agent_id)
    resp = _sync(client, [_record(delivery_id, agent_id, "delivered")], agent_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["errors"] == []
    assert body["resolved_records"][0]["status"] == "delivered"


# ---------- forged identity ----------

def test_agent_cannot_forge_another_agents_identity(client, signed_up_admin, auth_headers):
    code = signed_up_admin["org_invite_code"]
    victim_id, _ = _signup_agent(client, code, "sync_victim_agent")
    _, attacker_headers = _signup_agent(client, code, "sync_attacker_agent")
    delivery_id = _create_delivery(client, auth_headers, victim_id)

    # Attacker's token, but the payload claims to be the victim.
    resp = _sync(client, [_record(delivery_id, victim_id, "delivered")], attacker_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolved_records"] == []
    assert body["errors"][0]["code"] == "identity_mismatch"

    # And the delivery is untouched.
    got = client.get(f"/deliveries/{delivery_id}", headers=auth_headers)
    assert got.status_code == 200, got.text
    assert got.json()["status"] != "delivered"


def test_agent_cannot_update_delivery_assigned_to_someone_else(client, signed_up_admin, auth_headers):
    code = signed_up_admin["org_invite_code"]
    owner_id, _ = _signup_agent(client, code, "sync_owner_agent")
    other_id, other_headers = _signup_agent(client, code, "sync_other_agent")
    delivery_id = _create_delivery(client, auth_headers, owner_id)

    # Honest identity (own agent_id), but the delivery isn't theirs.
    resp = _sync(client, [_record(delivery_id, other_id, "delivered")], other_headers)
    body = resp.json()
    assert body["resolved_records"] == []
    assert body["errors"][0]["code"] == "not_assigned"


def test_conflict_response_never_leaks_other_agents_delivery(client, signed_up_admin, auth_headers):
    code = signed_up_admin["org_invite_code"]
    owner_id, _ = _signup_agent(client, code, "sync_leak_owner")
    other_id, other_headers = _signup_agent(client, code, "sync_leak_other")
    delivery_id = _create_delivery(client, auth_headers, owner_id)

    # Old timestamp would normally yield a "conflict" echoing the server's record.
    resp = _sync(client, [_record(delivery_id, other_id, "delivered", minutes=-60)], other_headers)
    body = resp.json()
    assert body["conflicts"] == [] and body["resolved_records"] == []
    assert body["errors"][0]["code"] == "not_assigned"


# ---------- cross-tenant ----------

def test_agent_cannot_touch_another_organizations_delivery(client, signed_up_admin, auth_headers):
    victim_agent, _ = _signup_agent(client, signed_up_admin["org_invite_code"], "sync_tenant_victim")
    delivery_id = _create_delivery(client, auth_headers, victim_agent)

    _, _, other_code = _second_org_admin(client)
    attacker_id, attacker_headers = _signup_agent(client, other_code, "sync_tenant_attacker")

    resp = _sync(client, [_record(delivery_id, attacker_id, "delivered")], attacker_headers)
    body = resp.json()
    assert body["resolved_records"] == []
    assert body["errors"][0]["code"] == "not_assigned"  # same generic answer as "doesn't exist"


def test_dispatcher_cannot_touch_another_organizations_delivery(client, signed_up_admin, auth_headers):
    agent_id, _ = _signup_agent(client, signed_up_admin["org_invite_code"], "sync_xorg_agent")
    delivery_id = _create_delivery(client, auth_headers, agent_id)

    other_admin, other_headers, _ = _second_org_admin(client)
    resp = _sync(client, [_record(delivery_id, agent_id, "cancelled")], other_headers)
    body = resp.json()
    assert body["resolved_records"] == []
    assert body["errors"][0]["code"] == "not_found"


def test_dispatcher_cannot_create_delivery_for_agent_in_another_org(client, signed_up_admin, auth_headers):
    foreign_agent, _ = _signup_agent(client, signed_up_admin["org_invite_code"], "sync_foreign_agent")
    _, other_headers, _ = _second_org_admin(client)
    resp = _sync(client, [_record(str(uuid.uuid4()), foreign_agent, "picked_up")], other_headers)
    body = resp.json()
    assert body["resolved_records"] == []
    assert body["errors"][0]["code"] == "invalid_agent"


def test_payload_org_id_is_ignored(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "sync_orgid_agent")
    delivery_id = _create_delivery(client, auth_headers, agent_id)
    resp = _sync(client, [_record(delivery_id, agent_id, "delivered", org_id="some-other-org")], agent_headers)
    body = resp.json()
    assert body["errors"] == []
    assert body["resolved_records"][0]["org_id"] == signed_up_admin["user"]["org_id"]


# ---------- unauthorized operations ----------

def test_agent_cannot_create_deliveries_via_sync(client, signed_up_admin):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "sync_create_agent")
    resp = _sync(client, [_record(str(uuid.uuid4()), agent_id, "picked_up")], agent_headers)
    body = resp.json()
    assert body["resolved_records"] == []
    assert body["errors"][0]["code"] == "not_assigned"


def test_agent_cannot_cancel_or_unassign_via_sync(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "sync_cancel_agent")
    delivery_id = _create_delivery(client, auth_headers, agent_id)
    assert _sync(client, [_record(delivery_id, agent_id, "out_for_delivery", minutes=1)], agent_headers).json()["errors"] == []
    for i, forbidden in enumerate(("cancelled", "pending")):
        body = _sync(client, [_record(delivery_id, agent_id, forbidden, minutes=5 + i)], agent_headers).json()
        assert body["resolved_records"] == []
        assert body["errors"][0]["code"] == "transition_not_allowed"


def test_agent_cannot_reopen_a_delivered_delivery(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "sync_reopen_agent")
    delivery_id = _create_delivery(client, auth_headers, agent_id)
    assert _sync(client, [_record(delivery_id, agent_id, "delivered", minutes=1)], agent_headers).json()["errors"] == []
    body = _sync(client, [_record(delivery_id, agent_id, "out_for_delivery", minutes=5)], agent_headers).json()
    assert body["resolved_records"] == []
    assert body["errors"][0]["code"] == "transition_not_allowed"


def test_sync_never_reassigns_an_existing_delivery(client, signed_up_admin, auth_headers):
    code = signed_up_admin["org_invite_code"]
    a_id, _ = _signup_agent(client, code, "sync_reassign_a")
    b_id, _ = _signup_agent(client, code, "sync_reassign_b")
    delivery_id = _create_delivery(client, auth_headers, a_id)
    # Dispatcher syncing a record whose payload agent_id names a different agent.
    body = _sync(client, [_record(delivery_id, b_id, "out_for_delivery")], auth_headers).json()
    assert body["errors"] == []
    assert body["resolved_records"][0]["agent_id"] == a_id


def test_dispatcher_can_create_and_update_in_own_org(client, signed_up_admin, auth_headers):
    agent_id, _ = _signup_agent(client, signed_up_admin["org_invite_code"], "sync_disp_agent")
    delivery_id = str(uuid.uuid4())
    body = _sync(client, [_record(delivery_id, agent_id, "picked_up")], auth_headers).json()
    assert body["errors"] == []
    assert body["resolved_records"][0]["agent_id"] == agent_id
    body = _sync(client, [_record(delivery_id, agent_id, "cancelled", minutes=5)], auth_headers).json()
    assert body["errors"] == []
    assert body["resolved_records"][0]["status"] == "cancelled"


# ---------- batch isolation, limits, timestamps, audit ----------

def test_bad_records_do_not_sink_or_piggyback_on_good_ones(client, signed_up_admin, auth_headers):
    code = signed_up_admin["org_invite_code"]
    me_id, me_headers = _signup_agent(client, code, "sync_batch_me")
    other_id, _ = _signup_agent(client, code, "sync_batch_other")
    mine = _create_delivery(client, auth_headers, me_id)
    theirs = _create_delivery(client, auth_headers, other_id)

    body = _sync(client, [
        _record(mine, me_id, "delivered"),
        _record(theirs, me_id, "delivered"),
    ], me_headers).json()
    assert [r["id"] for r in body["resolved_records"]] == [mine]
    assert [e["id"] for e in body["errors"]] == [theirs]


def test_batch_size_is_capped(client, signed_up_admin):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "sync_big_agent")
    records = [_record(str(uuid.uuid4()), agent_id) for _ in range(201)]
    assert _sync(client, records, agent_headers).status_code == 422


def test_far_future_timestamp_cannot_win_last_write_wins_forever(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup_agent(client, signed_up_admin["org_invite_code"], "sync_future_agent")
    delivery_id = _create_delivery(client, auth_headers, agent_id)
    far = _record(delivery_id, agent_id, "out_for_delivery", minutes=60 * 24 * 365)
    body = _sync(client, [far], agent_headers).json()
    assert body["errors"] == []
    stored = datetime.fromisoformat(body["resolved_records"][0]["updated_at"])
    assert stored <= datetime.utcnow() + timedelta(minutes=16)


def test_rejected_records_are_audited(client, signed_up_admin, auth_headers):
    code = signed_up_admin["org_invite_code"]
    victim_id, _ = _signup_agent(client, code, "sync_audit_victim")
    attacker_id, attacker_headers = _signup_agent(client, code, "sync_audit_attacker")
    delivery_id = _create_delivery(client, auth_headers, victim_id)
    _sync(client, [_record(delivery_id, victim_id, "delivered")], attacker_headers)

    events = client.get("/auth/security-events", headers=attacker_headers).json()
    assert any(e["event_type"] == "sync_rejected" and "identity_mismatch" in (e["detail"] or "") for e in events)
