"""Customer two-factor authentication (authenticator app + email code, recovery
codes) and customer account deletion (password, or typed email for Google-only)."""

import pyotp
import pytest
from sqlalchemy.orm import sessionmaker

from app.models.customer import CustomerDB


def _session_for(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()


def _login(client, payload):
    return client.post("/customer/login", json={"email": payload["email"], "password": payload["password"]})


def _enable_totp(client, headers):
    setup = client.post("/customer/2fa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    body = setup.json()
    assert body["otpauth_uri"].startswith("otpauth://totp/")  # what the QR code encodes
    resp = client.post("/customer/2fa/enable", json={"code": pyotp.TOTP(body["secret"]).now()}, headers=headers)
    assert resp.status_code == 200, resp.text
    return body["secret"], resp.json()["recovery_codes"]


@pytest.fixture()
def sent_2fa_codes(monkeypatch):
    from app.routes import customer_auth
    codes = []
    monkeypatch.setattr(customer_auth, "send_two_factor_code_email", lambda to, code, purpose: codes.append((purpose, code)))
    return codes


def test_2fa_is_off_by_default_and_login_is_unchanged(client, signed_up_customer, customer_auth_headers):
    assert client.get("/customer/2fa/status", headers=customer_auth_headers).json() == {"totp_enabled": False, "two_factor_method": "totp"}
    resp = _login(client, signed_up_customer["payload"])
    assert resp.status_code == 200 and resp.json()["access_token"] and resp.json()["requires_2fa"] is False


def test_2fa_endpoints_require_login(client):
    assert client.get("/customer/2fa/status").status_code == 401
    assert client.post("/customer/2fa/setup").status_code == 401


def test_authenticator_app_full_cycle(client, signed_up_customer, customer_auth_headers):
    secret, recovery = _enable_totp(client, customer_auth_headers)
    assert len(recovery) == 8

    # Password alone no longer gives a session.
    first = _login(client, signed_up_customer["payload"]).json()
    assert first["requires_2fa"] is True and first["two_factor_method"] == "totp"
    assert first["access_token"] is None and first["customer"] is None

    wrong = client.post("/customer/2fa/verify-login", json={"challenge_token": first["challenge_token"], "code": "000000"})
    assert wrong.status_code == 401

    ok = client.post("/customer/2fa/verify-login", json={"challenge_token": first["challenge_token"], "code": pyotp.TOTP(secret).now()})
    assert ok.status_code == 200, ok.text
    session = ok.json()
    assert session["access_token"] and session["customer"]["email"] == signed_up_customer["payload"]["email"]
    assert client.get("/customer/me", headers={"Authorization": f"Bearer {session['access_token']}"}).status_code == 200


def test_wrong_confirmation_code_does_not_turn_2fa_on(client, signed_up_customer, customer_auth_headers):
    assert client.post("/customer/2fa/setup", headers=customer_auth_headers).status_code == 200
    assert client.post("/customer/2fa/enable", json={"code": "000000"}, headers=customer_auth_headers).status_code == 400
    assert client.get("/customer/2fa/status", headers=customer_auth_headers).json()["totp_enabled"] is False
    assert _login(client, signed_up_customer["payload"]).json()["requires_2fa"] is False  # not locked out


def test_recovery_code_works_once(client, signed_up_customer, customer_auth_headers):
    _, recovery = _enable_totp(client, customer_auth_headers)
    challenge = _login(client, signed_up_customer["payload"]).json()["challenge_token"]
    assert client.post("/customer/2fa/verify-login", json={"challenge_token": challenge, "code": recovery[0]}).status_code == 200
    challenge = _login(client, signed_up_customer["payload"]).json()["challenge_token"]
    assert client.post("/customer/2fa/verify-login", json={"challenge_token": challenge, "code": recovery[0]}).status_code == 401


def test_email_code_method(sent_2fa_codes, client, signed_up_customer, customer_auth_headers):
    assert client.post("/customer/2fa/setup-email", headers=customer_auth_headers).status_code == 200
    purpose, code = sent_2fa_codes[-1]
    assert purpose == "enable_2fa"
    assert client.post("/customer/2fa/enable-email", json={"code": "999999" if code != "999999" else "111111"}, headers=customer_auth_headers).status_code == 400
    ok = client.post("/customer/2fa/enable-email", json={"code": code}, headers=customer_auth_headers)
    assert ok.status_code == 200 and len(ok.json()["recovery_codes"]) == 8

    first = _login(client, signed_up_customer["payload"]).json()
    assert first["requires_2fa"] and first["two_factor_method"] == "email" and "***" in first["masked_email"]
    purpose, login_code = sent_2fa_codes[-1]
    assert purpose == "login"
    assert client.post("/customer/2fa/resend-code", json={"challenge_token": first["challenge_token"], "code": ""}).status_code == 200
    login_code = sent_2fa_codes[-1][1]  # resend invalidates the earlier code
    done = client.post("/customer/2fa/verify-login", json={"challenge_token": first["challenge_token"], "code": login_code})
    assert done.status_code == 200 and done.json()["access_token"]


def test_turning_off_needs_the_password(client, signed_up_customer, customer_auth_headers):
    _enable_totp(client, customer_auth_headers)
    assert client.post("/customer/2fa/disable", json={"password": "wrong-password"}, headers=customer_auth_headers).status_code == 401
    assert client.get("/customer/2fa/status", headers=customer_auth_headers).json()["totp_enabled"] is True
    ok = client.post("/customer/2fa/disable", json={"password": signed_up_customer["payload"]["password"]}, headers=customer_auth_headers)
    assert ok.status_code == 200
    assert _login(client, signed_up_customer["payload"]).json()["requires_2fa"] is False


def test_challenge_tokens_are_not_interchangeable_with_staff(client, signed_up_customer, customer_auth_headers, signed_up_admin):
    from app.services.auth import create_two_factor_challenge_token
    secret, _ = _enable_totp(client, customer_auth_headers)
    staff_challenge = create_two_factor_challenge_token(signed_up_admin["user"]["id"])
    resp = client.post("/customer/2fa/verify-login", json={"challenge_token": staff_challenge, "code": pyotp.TOTP(secret).now()})
    assert resp.status_code == 401
    customer_challenge = _login(client, signed_up_customer["payload"]).json()["challenge_token"]
    assert client.post("/auth/2fa/verify-login", json={"challenge_token": customer_challenge, "code": "123456"}).status_code == 401


def test_google_only_account_cannot_enable_2fa(client, db_engine, signed_up_customer, customer_auth_headers):
    db = _session_for(db_engine)
    try:
        db.query(CustomerDB).filter(CustomerDB.id == signed_up_customer["customer"]["id"]).update({"has_usable_password": False})
        db.commit()
    finally:
        db.close()
    resp = client.post("/customer/2fa/setup", headers=customer_auth_headers)
    assert resp.status_code == 400 and "password" in resp.json()["detail"].lower()


# ---------- delete account ----------

def _delete(client, headers, **body):
    return client.request("DELETE", "/customer/account", json=body, headers=headers)


def test_customer_delete_needs_correct_password(client, signed_up_customer, customer_auth_headers):
    assert _delete(client, customer_auth_headers, password="wrong").status_code == 401
    assert _delete(client, customer_auth_headers).status_code == 401
    assert client.get("/customer/me", headers=customer_auth_headers).status_code == 200


def test_customer_delete_with_2fa_enabled_removes_everything(client, db_engine, signed_up_customer, customer_auth_headers):
    _enable_totp(client, customer_auth_headers)
    assert _delete(client, customer_auth_headers, password=signed_up_customer["payload"]["password"]).status_code == 200
    assert _login(client, signed_up_customer["payload"]).status_code == 401
    assert client.get("/customer/me", headers=customer_auth_headers).status_code in (401, 403, 404)


def test_google_only_customer_deletes_by_typing_their_email(client, db_engine, signed_up_customer, customer_auth_headers):
    db = _session_for(db_engine)
    try:
        db.query(CustomerDB).filter(CustomerDB.id == signed_up_customer["customer"]["id"]).update({"has_usable_password": False})
        db.commit()
    finally:
        db.close()
    assert _delete(client, customer_auth_headers, confirm="someone@else.com").status_code == 400
    assert _delete(client, customer_auth_headers, confirm=signed_up_customer["payload"]["email"].upper()).status_code == 200
