"""DELETE /auth/me — a staff member deleting their own account."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import sessionmaker

from app.models.delivery_history import DeliveryHistoryDB
from app.models.refresh_token import RefreshTokenDB
from app.models.user import UserDB


def _session_for(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()


def _signup(client, invite_code, username, role="agent"):
    resp = client.post("/auth/signup", json={
        "username": username, "email": f"{username}@example.com", "password": "correct-horse-battery",
        "role": role, "display_name": username.replace("_", " ").title(), "invite_code": invite_code,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["user"]["id"], {"Authorization": f"Bearer {body['access_token']}"}


def _delivery(client, auth_headers, agent_id, status="pending"):
    delivery_id, now = str(uuid.uuid4()), datetime.utcnow()
    resp = client.post("/deliveries/", json={
        "id": delivery_id, "agent_id": agent_id, "order_id": f"ORD-{uuid.uuid4().hex[:8]}", "status": status,
        "created_at": now.isoformat(), "updated_at": now.isoformat(),
    }, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return delivery_id


def _delete(client, headers, **body):
    return client.request("DELETE", "/auth/me", json=body, headers=headers)


def test_wrong_or_missing_password_is_refused(client, signed_up_admin, auth_headers):
    assert _delete(client, auth_headers, password="nope-nope-nope").status_code == 401
    assert _delete(client, auth_headers).status_code == 401
    assert client.get("/auth/me", headers=auth_headers).status_code == 200  # still there


def test_requires_login(client):
    assert client.request("DELETE", "/auth/me", json={"password": "x"}).status_code == 401


def test_sole_admin_can_delete_and_it_closes_the_org(client, db_engine, signed_up_admin, auth_headers):
    payload = signed_up_admin["payload"]
    resp = _delete(client, auth_headers, password=payload["password"])
    assert resp.status_code == 200, resp.text

    # Can't log in, old token is dead, sessions are gone.
    assert client.post("/auth/login", json={"username": payload["username"], "password": payload["password"]}).status_code == 401
    assert client.get("/auth/me", headers=auth_headers).status_code in (401, 403)

    db = _session_for(db_engine)
    try:
        user = db.query(UserDB).filter(UserDB.id == signed_up_admin["user"]["id"]).one()
        assert user.display_name == "Deleted User" and user.is_active is False
        assert payload["email"] not in user.email and payload["username"] != user.username
        assert db.query(RefreshTokenDB).filter(RefreshTokenDB.user_id == user.id).count() == 0
    finally:
        db.close()

    # The invite code of a closed org can't be used to join it any more.
    join = client.post("/auth/signup", json={
        "username": f"late_{uuid.uuid4().hex[:6]}", "email": f"late_{uuid.uuid4().hex[:6]}@example.com",
        "password": "correct-horse-battery", "role": "agent", "display_name": "Late", "invite_code": signed_up_admin["org_invite_code"],
    })
    assert join.status_code >= 400


def test_the_same_email_and_username_can_be_used_again_after_deleting(client, signed_up_admin, auth_headers):
    payload = signed_up_admin["payload"]
    assert _delete(client, auth_headers, password=payload["password"]).status_code == 200
    again = client.post("/auth/signup", json={**payload, "org_name": f"New_{uuid.uuid4().hex[:6]}"})
    assert again.status_code == 200, again.text


def test_only_admin_with_other_active_users_cannot_delete(client, signed_up_admin, auth_headers):
    _signup(client, signed_up_admin["org_invite_code"], f"colleague_{uuid.uuid4().hex[:6]}")
    resp = _delete(client, auth_headers, password=signed_up_admin["payload"]["password"])
    assert resp.status_code == 400 and "only admin" in resp.json()["detail"].lower()
    assert client.get("/auth/me", headers=auth_headers).status_code == 200


def test_agent_with_active_delivery_cannot_delete(client, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup(client, signed_up_admin["org_invite_code"], f"busy_{uuid.uuid4().hex[:6]}")
    _delivery(client, auth_headers, agent_id, status="out_for_delivery")
    resp = _delete(client, agent_headers, password="correct-horse-battery")
    assert resp.status_code == 400 and "in progress" in resp.json()["detail"].lower()


def test_agent_can_delete_and_their_name_is_removed_from_history(client, db_engine, signed_up_admin, auth_headers):
    agent_id, agent_headers = _signup(client, signed_up_admin["org_invite_code"], f"leaver_{uuid.uuid4().hex[:6]}")
    delivery_id = _delivery(client, auth_headers, agent_id)
    # The agent completes it via offline sync (writes a history row under their name).
    now = datetime.utcnow()
    sync = client.post("/sync", json={"records": [{
        "id": delivery_id, "agent_id": agent_id, "order_id": "ORD-X", "status": "delivered",
        "created_at": now.isoformat(), "updated_at": (now + timedelta(minutes=1)).isoformat(),
    }]}, headers=agent_headers)
    assert sync.status_code == 200 and sync.json()["errors"] == [], sync.text

    assert _delete(client, agent_headers, password="correct-horse-battery").status_code == 200

    db = _session_for(db_engine)
    try:
        rows = db.query(DeliveryHistoryDB).filter(DeliveryHistoryDB.changed_by_user_id == agent_id).all()
        assert rows and all(r.changed_by_display_name == "Deleted User" for r in rows)
    finally:
        db.close()


def test_google_only_account_must_type_username(client, db_engine, signed_up_admin, auth_headers):
    db = _session_for(db_engine)
    try:
        db.query(UserDB).filter(UserDB.id == signed_up_admin["user"]["id"]).update({"has_usable_password": False})
        db.commit()
    finally:
        db.close()
    assert _delete(client, auth_headers, confirm="wrong").status_code == 400
    assert _delete(client, auth_headers, confirm=signed_up_admin["payload"]["username"].upper()).status_code == 200
