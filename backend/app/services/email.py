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

import html
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.services import monitoring as monitoring_svc

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@deliverysync.local")
APP_URL = os.environ.get("FRONTEND_URL", "http://localhost:3700")


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


def send_verification_email(to_email: str, verify_link: str) -> None:
    subject = "Verify your Delivery Sync email address"
    body = (
        f"Please confirm this is your email address by clicking the link below "
        f"(expires in 48 hours):\n{verify_link}\n\n"
        f"If you didn't create a Delivery Sync account, you can safely ignore this email."
    )
    _send_email(to_email, subject, body)


def send_customer_verification_email(to_email: str, verify_link: str) -> None:
    subject = "Verify your Delivery Sync account email"
    body = (
        f"Please confirm this is your email address by clicking the link below "
        f"(expires in 48 hours):\n{verify_link}\n\n"
        f"If you didn't create a Delivery Sync account, you can safely ignore this email."
    )
    _send_email(to_email, subject, body)


def _html_email(heading: str, paragraphs: list[str], highlight: str | None = None, button_label: str | None = None, button_url: str | None = None, after: list[str] | None = None) -> str:
    """Small, dependency-free HTML email (inline CSS only, since email clients
    ignore stylesheets). All dynamic text is escaped."""
    e = html.escape
    body = "".join(f'<p style="margin:0 0 14px;line-height:1.55;">{e(p)}</p>' for p in paragraphs)
    tail = "".join(f'<p style="margin:0 0 14px;line-height:1.55;">{e(p)}</p>' for p in (after or []))
    box = (
        f'<p style="margin:18px 0;padding:14px 18px;background:#eef2ff;border-radius:8px;'
        f'font-family:monospace;font-size:20px;letter-spacing:2px;text-align:center;">{e(highlight)}</p>'
        if highlight else ""
    )
    button = (
        f'<p style="margin:22px 0;"><a href="{e(button_url)}" style="background:#4f46e5;color:#ffffff;'
        f'padding:12px 22px;border-radius:8px;text-decoration:none;font-weight:600;">{e(button_label)}</a></p>'
        if button_label and button_url else ""
    )
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#1f2937;max-width:560px;margin:0 auto;padding:24px;">'
        f'<h2 style="margin:0 0 18px;color:#4f46e5;">{e(heading)}</h2>{body}{box}{tail}{button}'
        '<p style="margin:24px 0 0;color:#6b7280;font-size:12px;">Delivery Sync</p></div>'
    )


def send_org_welcome_email(to_email: str, display_name: str, org_name: str, invite_code: str) -> None:
    """
    Sent once, right after POST /auth/signup (or the Google OAuth
    signup path) creates a BRAND NEW organization — the person who
    just created it is always its admin. Separate from
    send_verification_email (that one confirms the inbox is theirs; this
    one is the congratulatory "you're set up" message).
    """
    subject = f"Congratulations! Your Delivery Sync account for {org_name} is ready"
    intro = f"Congratulations, {display_name}! You've successfully created your Delivery Sync account, and your organization \"{org_name}\" is live with you as its admin."
    invite = "Share this invite code with your team so agents and dispatchers can join your organization from the Sign Up screen:"
    outro = "You can find this code again anytime in your account settings."
    body = f"Hi {display_name},\n\n{intro}\n\n{invite}\n\n    {invite_code}\n\n{outro}\n\nLog in: {APP_URL}\n"
    _send_email(to_email, subject, body, html_body=_html_email(
        "Congratulations — your account is ready!", [f"Hi {display_name},", intro, invite],
        highlight=invite_code, after=[outro], button_label="Open Delivery Sync", button_url=APP_URL,
    ))


def send_staff_welcome_email(to_email: str, display_name: str, org_name: str, role: str) -> None:
    """
    Sent once, right after someone JOINS an existing organization via
    invite code (see send_org_welcome_email for the create-an-org case)
    as an agent or dispatcher.
    """
    role_label = "a dispatcher" if role == "dispatcher" else "an agent"
    subject = f"Congratulations! Welcome to {org_name} on Delivery Sync"
    intro = f"Congratulations, {display_name}! You've successfully created your Delivery Sync account and joined \"{org_name}\" as {role_label}."
    nxt = "You can log in now with the username and password you just created."
    body = f"Hi {display_name},\n\n{intro}\n\n{nxt}\n\nLog in: {APP_URL}\n"
    _send_email(to_email, subject, body, html_body=_html_email(
        "Congratulations — welcome aboard!", [f"Hi {display_name},", intro, nxt],
        button_label="Log in to Delivery Sync", button_url=APP_URL,
    ))


def send_customer_welcome_email(to_email: str, display_name: str) -> None:
    """Sent once, right after a customer account is created (email signup or Google)."""
    subject = "Congratulations! Your Delivery Sync account is ready"
    intro = f"Congratulations, {display_name}! You've successfully created your Delivery Sync customer account."
    nxt = "You can now browse stores, place orders, and track every delivery live from your dashboard."
    body = f"Hi {display_name},\n\n{intro}\n\n{nxt}\n\nOpen Delivery Sync: {APP_URL}\n"
    _send_email(to_email, subject, body, html_body=_html_email(
        "Congratulations — your account is ready!", [f"Hi {display_name},", intro, nxt],
        button_label="Open Delivery Sync", button_url=APP_URL,
    ))


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


def send_security_alert_email(to_email: str, event_description: str, ip_address: str = None, device_info: str = None) -> None:
    """
    Phase 17: a plain security notification — new/suspicious login,
    password changed, 2FA disabled, all sessions logged out. Same
    dev-mode "print instead of send" fallback as every other email in
    this module via _send_email, so this works out of the box without
    SMTP credentials.
    """
    subject = f"Delivery Sync security alert: {event_description}"
    context_lines = []
    if device_info:
        context_lines.append(f"Device: {device_info}")
    if ip_address:
        context_lines.append(f"IP address: {ip_address}")
    context = ("\n" + "\n".join(context_lines) + "\n") if context_lines else ""
    body = (
        f"We noticed the following on your Delivery Sync account:\n\n"
        f"    {event_description}\n"
        f"{context}\n"
        f"If this was you, no action is needed. If you don't recognize this, change your password immediately "
        f"and review your active sessions."
    )
    _send_email(to_email, subject, body)


def _send_email(to_email: str, subject: str, body: str, html_body: str | None = None) -> None:
    if not SMTP_HOST:
        # No SMTP configured — this is the default, zero-cost path.
        print("=" * 60)
        print("EMAIL (no SMTP_HOST configured — printed instead of sent)")
        print(f"To: {to_email}")
        print(f"Subject: {subject}")
        print(body)
        print("=" * 60)
        monitoring_svc.record_notification_sent("email", success=True)
        return

    # SMTP_HOST is configured — actually send a real email. With an HTML
    # version, send multipart/alternative (plain text first, HTML preferred).
    if html_body:
        message = MIMEMultipart("alternative")
        message.attach(MIMEText(body, "plain", "utf-8"))
        message.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = FROM_EMAIL
    message["To"] = to_email

    try:
        # Port 465 = implicit TLS; anything else (587) = STARTTLS. The
        # timeout stops a slow/unreachable SMTP server hanging the request.
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
            server.starttls()
        with server:
            if SMTP_USERNAME and SMTP_PASSWORD:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, [to_email], message.as_string())
        monitoring_svc.record_notification_sent("email", success=True)
    except Exception:
        monitoring_svc.record_notification_sent("email", success=False)
        raise
