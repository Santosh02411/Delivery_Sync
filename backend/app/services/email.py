"""
Email sending, abstracted behind one function so the rest of the app
never needs to know HOW an email actually gets delivered. Used for
password reset links and delivery status-change notifications.

This project has no budget for a paid transactional email service, so by
default, "sending" an email just prints it clearly to the backend
console/log instead of actually delivering it — which is enough to
develop and test the full flow (token generation and expiry for resets;
status text and tracking link for notifications) without needing any
external service at all.

If SMTP_HOST (and the other SMTP_* variables below) are set in the
environment, this switches to actually sending via any standard SMTP
server — for example, a free Gmail account with an "app password" works
here at zero cost. Nothing about the calling code changes based on which
path is used; only this module's internal behavior does.
"""

import os
import smtplib
from email.mime.text import MIMEText

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@deliverysync.local")


def send_password_reset_email(to_email: str, reset_link: str) -> None:
    subject = "Reset your Delivery Sync password"
    body = (
        f"Someone requested a password reset for this email address.\n\n"
        f"Reset your password here (link expires in 30 minutes):\n{reset_link}\n\n"
        f"If you didn't request this, you can safely ignore this email."
    )
    _send_email(to_email, subject, body)


def send_customer_password_reset_email(to_email: str, reset_link: str) -> None:
    subject = "Reset your Delivery Sync account password"
    body = (
        f"Someone requested a password reset for your Delivery Sync customer account.\n\n"
        f"Reset your password here (link expires in 30 minutes):\n{reset_link}\n\n"
        f"If you didn't request this, you can safely ignore this email — your password won't be changed."
    )
    _send_email(to_email, subject, body)


def send_status_notification_email(to_email: str, order_id: str, status_label: str, tracking_link: str) -> None:
    subject = f"Update on your delivery {order_id}"
    body = (
        f"Your order {order_id} is now: {status_label}\n\n"
        f"Track it live here (no login needed):\n{tracking_link}"
    )
    _send_email(to_email, subject, body)


def send_two_factor_code_email(to_email: str, code: str, purpose: str) -> None:
    """
    purpose is "enable_2fa" (confirming the user controls this inbox
    before turning email-based 2FA on) or "login" (a normal sign-in
    second factor) — same email shape either way, just different wording
    so it's clear from the subject line why the code was sent.
    """
    if purpose == "enable_2fa":
        subject = "Confirm email for Delivery Sync two-factor authentication"
        intro = "Use this code to turn on email-based two-factor authentication for your account:"
    else:
        subject = "Your Delivery Sync login code"
        intro = "Use this code to finish logging in:"

    body = (
        f"{intro}\n\n"
        f"    {code}\n\n"
        f"This code expires in 10 minutes. If you didn't request this, you can safely ignore this email."
    )
    _send_email(to_email, subject, body)


def _send_email(to_email: str, subject: str, body: str) -> None:
    if not SMTP_HOST:
        # No SMTP configured — this is the default, zero-cost path.
        print("=" * 60)
        print("EMAIL (no SMTP_HOST configured — printed instead of sent)")
        print(f"To: {to_email}")
        print(f"Subject: {subject}")
        print(body)
        print("=" * 60)
        return

    # SMTP_HOST is configured — actually send a real email.
    message = MIMEText(body)
    message["Subject"] = subject
    message["From"] = FROM_EMAIL
    message["To"] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        if SMTP_USERNAME and SMTP_PASSWORD:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(FROM_EMAIL, [to_email], message.as_string())
