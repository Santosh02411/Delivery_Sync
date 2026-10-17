"""
Tests for email verification by ONE-TIME CODE (OTP) — routes/auth.py's
/verify-email + /resend-verification (staff) and the customer equivalents
in routes/customer_auth.py, backed by services/email_verification.py.

Covers: a fresh signup is unverified and can still log in; the emailed
6-digit code verifies; wrong/expired/reused codes are refused; a code is
burned after 5 wrong guesses; only the newest code works; codes are bound
to the signed-in account and can't cross between staff and customers.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.email_otp import EmailOtpDB


def _session_for(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()


@pytest.fixture()
def sent_codes(monkeypatch):
    """Captures every emailed verification code. List this fixture FIRST in a
    test's arguments so it's active before the signup fixtures run."""
    from app.routes import auth as auth_routes, customer_auth

    codes = []
    monkeypatch.setattr(auth_routes, "send_verification_email", lambda to, code: codes.append(code))
    monkeypatch.setattr(customer_auth, "send_customer_verification_email", lambda to, code: codes.append(code))
    return codes


def test_staff_signup_is_unverified_and_login_still_works(client, admin_signup_payload):
    resp = client.post("/auth/signup", json=admin_signup_payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["email_verified"] is False
    resp = client.post("/auth/login", json={"username": admin_signup_payload["username"], "password": admin_signup_payload["password"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["email_verified"] is False  # verification never blocks login


def test_signup_emails_a_six_digit_code(sent_codes, client, signed_up_admin):
    assert len(sent_codes) == 1
    assert len(sent_codes[0]) == 6 and sent_codes[0].isdigit()


def test_staff_verify_email_full_cycle(sent_codes, client, signed_up_admin, auth_headers):
    code = sent_codes[-1]
    resp = client.post("/auth/verify-email", json={"code": code}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert client.get("/auth/me", headers=auth_headers).json()["email_verified"] is True


def test_staff_code_requires_login_and_is_single_use(sent_codes, client, signed_up_admin, auth_headers):
    code = sent_codes[-1]
    assert client.post("/auth/verify-email", json={"code": code}).status_code == 401  # not signed in
    assert client.post("/auth/verify-email", json={"code": code}, headers=auth_headers).status_code == 200
    # Already verified: a second call is a harmless no-op, and the code is spent.
    again = client.post("/auth/verify-email", json={"code": code}, headers=auth_headers)
    assert again.status_code == 200 and "already verified" in again.json()["message"].lower()


def test_wrong_code_is_rejected_and_counts_attempts(sent_codes, client, signed_up_admin, auth_headers):
    real = sent_codes[-1]
    wrong = "000000" if real != "000000" else "111111"
    resp = client.post("/auth/verify-email", json={"code": wrong}, headers=auth_headers)
    assert resp.status_code == 400
    assert "4 attempts left" in resp.json()["detail"]
    assert client.get("/auth/me", headers=auth_headers).json()["email_verified"] is False


def test_code_is_burned_after_five_wrong_guesses(sent_codes, client, signed_up_admin, auth_headers):
    real = sent_codes[-1]
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        assert client.post("/auth/verify-email", json={"code": wrong}, headers=auth_headers).status_code == 400
    # Even the CORRECT code no longer works — a new one must be requested.
    resp = client.post("/auth/verify-email", json={"code": real}, headers=auth_headers)
    assert resp.status_code == 400
    assert client.post("/auth/resend-verification", headers=auth_headers).status_code == 200
    assert client.post("/auth/verify-email", json={"code": sent_codes[-1]}, headers=auth_headers).status_code == 200


def test_expired_code_is_rejected(sent_codes, client, db_engine, signed_up_admin, auth_headers):
    db = _session_for(db_engine)
    try:
        db.query(EmailOtpDB).filter(EmailOtpDB.user_id == signed_up_admin["user"]["id"]).update(
            {"expires_at": datetime.utcnow() - timedelta(minutes=1)}
        )
        db.commit()
    finally:
        db.close()
    resp = client.post("/auth/verify-email", json={"code": sent_codes[-1]}, headers=auth_headers)
    assert resp.status_code == 400 and "expired" in resp.json()["detail"].lower()


def test_resend_invalidates_the_older_code(sent_codes, client, signed_up_admin, auth_headers):
    first = sent_codes[-1]
    assert client.post("/auth/resend-verification", headers=auth_headers).status_code == 200
    second = sent_codes[-1]
    if first != second:  # (1-in-a-million collision otherwise)
        assert client.post("/auth/verify-email", json={"code": first}, headers=auth_headers).status_code == 400
    assert client.post("/auth/verify-email", json={"code": second}, headers=auth_headers).status_code == 200


def test_code_is_stored_hashed_never_plain(sent_codes, client, db_engine, signed_up_admin):
    db = _session_for(db_engine)
    try:
        rows = db.query(EmailOtpDB).filter(EmailOtpDB.user_id == signed_up_admin["user"]["id"]).all()
        assert rows and all(r.code_hash != sent_codes[-1] and sent_codes[-1] not in r.code_hash for r in rows)
    finally:
        db.close()


def test_resend_short_circuits_if_already_verified(sent_codes, client, signed_up_admin, auth_headers):
    client.post("/auth/verify-email", json={"code": sent_codes[-1]}, headers=auth_headers)
    resp = client.post("/auth/resend-verification", headers=auth_headers)
    assert resp.status_code == 200 and "already verified" in resp.json()["message"].lower()


def test_resend_verification_requires_auth(client):
    assert client.post("/auth/resend-verification").status_code == 401


def test_customer_signup_is_unverified_and_login_still_works(client, customer_signup_payload):
    resp = client.post("/customer/signup", json=customer_signup_payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["customer"]["email_verified"] is False
    resp = client.post("/customer/login", json={"email": customer_signup_payload["email"], "password": customer_signup_payload["password"]})
    assert resp.status_code == 200 and resp.json()["customer"]["email_verified"] is False


def test_customer_verify_email_full_cycle(sent_codes, client, signed_up_customer, customer_auth_headers):
    code = sent_codes[-1]
    resp = client.post("/customer/verify-email", json={"code": code}, headers=customer_auth_headers)
    assert resp.status_code == 200, resp.text
    assert client.get("/customer/me", headers=customer_auth_headers).json()["email_verified"] is True


def test_customer_wrong_code_and_auth_required(sent_codes, client, signed_up_customer, customer_auth_headers):
    real = sent_codes[-1]
    wrong = "000000" if real != "000000" else "111111"
    assert client.post("/customer/verify-email", json={"code": wrong}, headers=customer_auth_headers).status_code == 400
    assert client.post("/customer/verify-email", json={"code": real}).status_code == 401


def test_customer_resend_verification(sent_codes, client, signed_up_customer, customer_auth_headers):
    before = len(sent_codes)
    resp = client.post("/customer/resend-verification", headers=customer_auth_headers)
    assert resp.status_code == 200, resp.text
    assert len(sent_codes) == before + 1


def test_staff_and_customer_codes_are_not_interchangeable(sent_codes, client, signed_up_admin, auth_headers, signed_up_customer, customer_auth_headers):
    staff_code = sent_codes[0]
    customer_code = sent_codes[1]
    if staff_code != customer_code:
        assert client.post("/customer/verify-email", json={"code": staff_code}, headers=customer_auth_headers).status_code == 400
        assert client.post("/auth/verify-email", json={"code": customer_code}, headers=auth_headers).status_code == 400
