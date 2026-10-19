"""Welcome / congratulations message for EVERY kind of new account.

Email/password signups get ONE email that both congratulates and carries the
verification code (sending a second, separate welcome email right behind it is
what failed to arrive for some mail providers). Google sign-ups (no code needed)
get a stand-alone welcome email.
"""

import uuid


def test_customer_signup_sends_one_combined_email(client, monkeypatch):
    from app.routes import customer_auth

    sent = []
    monkeypatch.setattr(customer_auth, "send_customer_verification_email", lambda to, code, welcome=None: sent.append((to, code, welcome)))
    email = f"welcome_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post("/customer/signup", json={"email": email, "password": "correct-horse-battery", "name": "Ravi"})
    assert resp.status_code == 200, resp.text
    assert len(sent) == 1 and sent[0][0] == email and sent[0][2] == {"name": "Ravi"}


def test_resend_sends_only_the_code_not_the_greeting(client, signed_up_customer, customer_auth_headers, monkeypatch):
    from app.routes import customer_auth

    sent = []
    monkeypatch.setattr(customer_auth, "send_customer_verification_email", lambda to, code, welcome=None: sent.append(welcome))
    assert client.post("/customer/resend-verification", headers=customer_auth_headers).status_code == 200
    assert sent == [None]


def test_combined_email_has_congratulations_code_and_invite_code(monkeypatch):
    from app.services import email as email_svc

    sent = []
    monkeypatch.setattr(email_svc, "_send_email", lambda to, subject, body, html_body=None: sent.append((to, subject, body, html_body)))
    email_svc.send_verification_email("a@example.com", "482913", welcome={
        "name": "Asha <b>", "org_name": "Acme", "role": "admin", "is_new_org": True, "invite_code": "ABCD1234",
    })
    to, subject, body, html_body = sent[0]
    assert "482913" in subject and "!" not in subject  # calm, transactional subject (hype words trip spam filters)
    assert "Congratulations" in body and "482913" in body and "ABCD1234" in body and "Acme" in body
    assert "ABCD1234" in html_body and "<b>" not in html_body and "&lt;b&gt;" in html_body  # escaped

    # a plain resend has no greeting
    sent.clear()
    email_svc.send_verification_email("a@example.com", "111222")
    assert "Congratulations" not in sent[0][2]


def test_joiner_and_customer_greetings(monkeypatch):
    from app.services import email as email_svc

    sent = []
    monkeypatch.setattr(email_svc, "_send_email", lambda to, subject, body, html_body=None: sent.append(body))
    email_svc.send_verification_email("g@example.com", "123456", welcome={"name": "Gita", "org_name": "Acme", "role": "agent", "is_new_org": False})
    email_svc.send_customer_verification_email("c@example.com", "654321", welcome={"name": "Ravi"})
    assert 'joined "Acme" as an agent' in sent[0]
    assert "browse stores" in sent[1]


def test_standalone_welcome_emails_still_exist_for_google_signups(monkeypatch):
    from app.services import email as email_svc

    sent = []
    monkeypatch.setattr(email_svc, "_send_email", lambda to, subject, body, html_body=None: sent.append((subject, body)))
    email_svc.send_org_welcome_email("a@example.com", "Asha", "Acme", "ABCD1234")
    email_svc.send_staff_welcome_email("g@example.com", "Gita", "Acme", "dispatcher")
    email_svc.send_customer_welcome_email("c@example.com", "Ravi")
    assert all("Congratulations" in body for _, body in sent) and "ABCD1234" in sent[0][1]
    assert all("!" not in subject for subject, _ in sent)


def test_smtp_settings_are_forgiving_about_common_env_mistakes(monkeypatch):
    from app.services import email as email_svc

    assert email_svc._port("587") == 587
    assert email_svc._port(" 587   # 465 also works") == 587
    assert email_svc._port(None) == 587

    monkeypatch.setattr(email_svc, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(email_svc, "SMTP_USERNAME", "me@gmail.com")
    monkeypatch.setattr(email_svc, "FROM_EMAIL", "me@gmail.com")
    monkeypatch.setattr(email_svc, "SMTP_PASSWORD", "abcdefghijklmnop")
    assert email_svc.email_config_problems() == []

    monkeypatch.setattr(email_svc, "SMTP_PASSWORD", "abcdefghijklmnop(real)")  # junk left on the line
    assert any("16 letters" in p for p in email_svc.email_config_problems())

    monkeypatch.setattr(email_svc, "SMTP_HOST", None)
    assert "SMTP_HOST is not set" in email_svc.email_config_problems()[0]


def test_transient_smtp_error_is_retried_once_but_auth_errors_are_not(monkeypatch):
    import smtplib
    from app.services import email as email_svc

    monkeypatch.setattr(email_svc, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(email_svc, "SMTP_USERNAME", "u")
    monkeypatch.setattr(email_svc, "SMTP_PASSWORD", "p")
    monkeypatch.setattr(email_svc.time, "sleep", lambda s: None)

    attempts = {"n": 0}

    class Flaky:
        def __init__(self, *a, **k): pass
        def starttls(self): pass
        def login(self, u, p):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise smtplib.SMTPResponseException(454, b"4.7.0 Too many login attempts, try again later")
        def sendmail(self, *a): return {}
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(email_svc.smtplib, "SMTP", Flaky)
    email_svc._send_email("x@example.com", "s", "b")
    assert attempts["n"] == 2  # failed once, retried, delivered

    attempts["n"] = 0

    class BadAuth(Flaky):
        def login(self, u, p):
            attempts["n"] += 1
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    monkeypatch.setattr(email_svc.smtplib, "SMTP", BadAuth)
    try:
        email_svc._send_email("x@example.com", "s", "b")
        raise AssertionError("should have raised")
    except smtplib.SMTPAuthenticationError:
        pass
    assert attempts["n"] == 1  # permanent error: no pointless retry


def test_message_has_the_headers_that_help_inbox_placement(monkeypatch):
    from app.services import email as email_svc

    monkeypatch.setattr(email_svc, "FROM_EMAIL", "team@example.com")
    monkeypatch.setattr(email_svc, "FROM_NAME", "Delivery Sync")
    message = email_svc._build_message("a@example.com", "Hello", "plain", "<p>html</p>")
    assert message["From"] == "Delivery Sync <team@example.com>"
    assert message["Reply-To"] == "team@example.com"
    assert message["Date"] and message["Message-ID"].endswith("@example.com>")
    assert message["Auto-Submitted"] == "auto-generated"
    assert [p.get_content_type() for p in message.get_payload()] == ["text/plain", "text/html"]
