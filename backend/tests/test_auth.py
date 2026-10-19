"""Staff auth: signup (new org vs invite code), login, and the
admin-self-assignment guard on invite-code signups."""

from app.routes import auth as auth_routes


def test_signup_creating_org_sends_one_combined_welcome_and_code_email(client, admin_signup_payload, monkeypatch):
    calls = []
    monkeypatch.setattr(auth_routes, "send_verification_email", lambda to, code, welcome=None: calls.append((to, code, welcome)))

    resp = client.post("/auth/signup", json=admin_signup_payload)
    assert resp.status_code == 200
    invite_code = resp.json()["org_invite_code"]

    assert len(calls) == 1  # ONE email: congratulations + verification code together
    to_email, code, welcome = calls[0]
    assert to_email == admin_signup_payload["email"] and len(code) == 6
    assert welcome["is_new_org"] is True
    assert welcome["org_name"] == admin_signup_payload["org_name"]
    assert welcome["invite_code"] == invite_code  # the SAME code returned in the response, not a different one


def test_signup_joining_org_sends_staff_greeting_not_org_one(client, admin_signup_payload, monkeypatch):
    calls = []
    monkeypatch.setattr(auth_routes, "send_verification_email", lambda to, code, welcome=None: calls.append((to, code, welcome)))

    signup_resp = client.post("/auth/signup", json=admin_signup_payload)
    invite_code = signup_resp.json()["org_invite_code"]
    calls.clear()  # only care about the JOIN below, not the org-creation email above

    joiner_payload = {
        "username": "joiner1", "email": "joiner@example.com", "password": "password123",
        "display_name": "Joiner One", "role": "dispatcher", "invite_code": invite_code,
    }
    resp = client.post("/auth/signup", json=joiner_payload)
    assert resp.status_code == 200

    assert len(calls) == 1
    to_email, _code, welcome = calls[0]
    assert to_email == "joiner@example.com"
    assert welcome["is_new_org"] is False and welcome["role"] == "dispatcher"
    assert welcome["org_name"] == admin_signup_payload["org_name"]


def test_signup_creates_org_and_returns_token(client, admin_signup_payload):
    resp = client.post("/auth/signup", json=admin_signup_payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["user"]["role"] == "admin"
    assert body["org_invite_code"]  # only present when creating a new org


def test_signup_duplicate_username_rejected(client, admin_signup_payload):
    client.post("/auth/signup", json=admin_signup_payload)
    resp = client.post("/auth/signup", json=admin_signup_payload)
    assert resp.status_code == 400
    assert "username" in resp.json()["detail"].lower()


def test_signup_short_password_rejected(client, admin_signup_payload):
    admin_signup_payload["password"] = "abc"
    resp = client.post("/auth/signup", json=admin_signup_payload)
    assert resp.status_code == 400


def test_signup_requires_org_name_or_invite_code(client, admin_signup_payload):
    admin_signup_payload.pop("org_name")
    resp = client.post("/auth/signup", json=admin_signup_payload)
    assert resp.status_code == 400


def test_login_with_valid_credentials(client, signed_up_admin):
    payload = signed_up_admin["payload"]
    resp = client.post(
        "/auth/login",
        json={"username": payload["username"], "password": payload["password"]},
    )
    assert resp.status_code == 200


def test_login_with_wrong_password_rejected(client, signed_up_admin):
    payload = signed_up_admin["payload"]
    resp = client.post(
        "/auth/login",
        json={"username": payload["username"], "password": "totally-wrong"},
    )
    assert resp.status_code in (400, 401)


def test_joining_via_invite_code_gets_requested_role(client, signed_up_admin):
    invite_code = signed_up_admin["org_invite_code"]
    resp = client.post(
        "/auth/signup",
        json={
            "username": "agent_joiner",
            "email": "agent_joiner@example.com",
            "password": "correct-horse-battery",
            "role": "agent",
            "display_name": "Joining Agent",
            "invite_code": invite_code,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "agent"
    assert resp.json()["user"]["org_id"] == signed_up_admin["user"]["org_id"]


def test_cannot_self_assign_admin_via_invite_code(client, signed_up_admin):
    """Security-critical: joining an org via invite code must never let
    someone grant themselves admin over someone else's organization."""
    invite_code = signed_up_admin["org_invite_code"]
    resp = client.post(
        "/auth/signup",
        json={
            "username": "sneaky_admin",
            "email": "sneaky_admin@example.com",
            "password": "correct-horse-battery",
            "role": "admin",
            "display_name": "Sneaky",
            "invite_code": invite_code,
        },
    )
    assert resp.status_code == 400


def test_invalid_invite_code_rejected(client):
    resp = client.post(
        "/auth/signup",
        json={
            "username": "nobody",
            "email": "nobody@example.com",
            "password": "correct-horse-battery",
            "role": "agent",
            "display_name": "Nobody",
            "invite_code": "THIS-CODE-DOES-NOT-EXIST",
        },
    )
    assert resp.status_code == 400


def test_authenticated_endpoint_rejects_missing_token(client):
    resp = client.get("/auth/2fa/status")
    assert resp.status_code in (401, 403)


def test_authenticated_endpoint_accepts_valid_token(client, auth_headers):
    resp = client.get("/auth/2fa/status", headers=auth_headers)
    assert resp.status_code == 200
