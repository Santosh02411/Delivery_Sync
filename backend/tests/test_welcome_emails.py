"""Welcome / congratulations email goes out for EVERY kind of new account."""

import uuid


def test_customer_signup_sends_welcome_email(client, monkeypatch):
    from app.routes import customer_auth

    sent = []
    monkeypatch.setattr(customer_auth, "send_customer_welcome_email", lambda *args: sent.append(args))
    email = f"welcome_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post("/customer/signup", json={"email": email, "password": "correct-horse-battery", "name": "Ravi"})
    assert resp.status_code == 200, resp.text
    assert sent == [(email, "Ravi")]


def test_customer_welcome_failure_never_blocks_signup(client, monkeypatch):
    from app.routes import customer_auth

    def boom(*args):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(customer_auth, "send_customer_welcome_email", boom)
    email = f"welcome_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post("/customer/signup", json={"email": email, "password": "correct-horse-battery", "name": "Ravi"})
    assert resp.status_code == 200, resp.text


def test_staff_signup_sends_matching_welcome(client, admin_signup_payload, monkeypatch):
    from app.routes import auth as auth_routes

    org_calls, staff_calls = [], []
    monkeypatch.setattr(auth_routes, "send_org_welcome_email", lambda *a: org_calls.append(a))
    monkeypatch.setattr(auth_routes, "send_staff_welcome_email", lambda *a: staff_calls.append(a))

    body = client.post("/auth/signup", json=admin_signup_payload).json()
    assert len(org_calls) == 1 and org_calls[0][0] == admin_signup_payload["email"]

    for role in ("agent", "dispatcher"):
        name = f"{role}_{uuid.uuid4().hex[:6]}"
        resp = client.post("/auth/signup", json={
            "username": name, "email": f"{name}@example.com", "password": "correct-horse-battery",
            "role": role, "display_name": name, "invite_code": body["org_invite_code"],
        })
        assert resp.status_code == 200, resp.text
    assert [c[3] for c in staff_calls] == ["agent", "dispatcher"]


def test_welcome_email_content_is_congratulatory_html_and_plain(monkeypatch):
    from app.services import email as email_svc

    sent = []
    monkeypatch.setattr(email_svc, "_send_email", lambda to, subject, body, html_body=None: sent.append((to, subject, body, html_body)))
    email_svc.send_customer_welcome_email("c@example.com", "Ravi <script>")
    to, subject, body, html_body = sent[0]
    assert to == "c@example.com" and "Congratulations" in subject and "Congratulations" in body
    assert "<script>" not in html_body and "&lt;script&gt;" in html_body  # escaped
