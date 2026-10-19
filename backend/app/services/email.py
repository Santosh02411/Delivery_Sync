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
import re
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

from app.services import monitoring as monitoring_svc

def _env(name: str, default: str | None = None) -> str | None:
    """Reads an env var, trimming whitespace and any wrapping quotes — a stray
    space or '"' in .env is the most common reason a correct-looking setting fails."""
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip().strip('"').strip("'").strip()
    return value or default


def _port(raw: str | None) -> int:
    """'587', ' 587 ' and '587 # comment' all mean 587."""
    match = re.match(r"\s*(\d+)", raw or "")
    return int(match.group(1)) if match else 587


SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = _port(os.environ.get("SMTP_PORT"))
SMTP_USERNAME = _env("SMTP_USERNAME")
# Gmail shows app passwords as four groups ("abcd efgh ijkl mnop"); the spaces
# are only for readability, so they're removed here.
SMTP_PASSWORD = re.sub(r"\s+", "", _env("SMTP_PASSWORD") or "") or None
# From address defaults to the login account: Gmail rewrites/rejects a From
# that isn't the authenticated account, so "noreply@deliverysync.local" would never work.
FROM_EMAIL = _env("FROM_EMAIL") or SMTP_USERNAME or "noreply@deliverysync.local"
APP_URL = _env("FRONTEND_URL", "http://localhost:3700")
FROM_NAME = _env("FROM_NAME", "Delivery Sync")
SMTP_DEBUG = (_env("SMTP_DEBUG", "") or "").lower() in ("1", "true", "yes")


def email_config_problems() -> list[str]:
    """Human-readable problems with the current SMTP settings (empty list = looks fine)."""
    problems = []
    if not SMTP_HOST:
        return ["SMTP_HOST is not set, so emails are only printed in this console, never sent."]
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        problems.append("SMTP_USERNAME / SMTP_PASSWORD are missing.")
    if "gmail" in SMTP_HOST.lower():
        if SMTP_PASSWORD and not re.fullmatch(r"[A-Za-z]{16}", SMTP_PASSWORD):
            problems.append(
                f"SMTP_PASSWORD has {len(SMTP_PASSWORD)} characters, but a Gmail app password is exactly 16 letters. "
                "Use an App Password (not your normal Gmail password) and keep the line clean: "
                "SMTP_PASSWORD=abcdefghijklmnop with nothing after it."
            )
        if SMTP_USERNAME and FROM_EMAIL.lower() != SMTP_USERNAME.lower():
            problems.append("FROM_EMAIL should be the same address as SMTP_USERNAME for Gmail.")
    return problems


def describe_email_config() -> str:
    if not SMTP_HOST:
        return "Email: SMTP not configured — emails are printed here instead of sent."
    problems = email_config_problems()
    base = f"Email: SMTP {SMTP_HOST}:{SMTP_PORT} as {SMTP_USERNAME or '(no user)'}, from {FROM_EMAIL}."
    return base + ("  WARNING: " + " ".join(problems) if problems else "  Looks configured.")


print(describe_email_config())  # shows once at startup, so a missing/misread .env is obvious


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


def _welcome_intro(welcome: dict) -> list[str]:
    """Congratulation lines for a brand-new account. `welcome` keys: name, and (staff only)
    org_name, role, is_new_org, invite_code. Shared by the combined signup email below
    and the stand-alone welcome emails (used for Google sign-ups, which need no code)."""
    name = welcome.get("name") or "there"
    org_name = welcome.get("org_name")
    if not org_name:  # customer
        return [f"Hi {name},",
                f"Congratulations, {name}! You've successfully created your Delivery Sync account.",
                "You can now browse stores, place orders, and track every delivery live from your dashboard."]
    if welcome.get("is_new_org"):
        lines = [f"Hi {name},",
                 f"Congratulations, {name}! You've successfully created your Delivery Sync account, "
                 f"and your organization \"{org_name}\" is live with you as its admin."]
        if welcome.get("invite_code"):
            lines.append("Share this invite code with your team so agents and dispatchers can join from the Sign Up screen: "
                         f"{welcome['invite_code']}")
        return lines
    role_label = "a dispatcher" if welcome.get("role") == "dispatcher" else "an agent"
    return [f"Hi {name},",
            f"Congratulations, {name}! You've successfully created your Delivery Sync account "
            f"and joined \"{org_name}\" as {role_label}."]


def _send_verification_code(to_email: str, code: str, who: str, welcome: dict | None = None) -> None:
    """The 6-digit verification email. At SIGNUP it also carries the congratulations message
    (`welcome`), so the new person gets ONE email that both greets them and verifies them —
    instead of two emails sent back-to-back, where the second can be throttled or filtered
    by the mail provider. A later 'resend code' passes no `welcome` and sends just the code."""
    tail = ["It expires in 10 minutes and can be used once. Never share this code with anyone.",
            f"If you didn't create a Delivery Sync {who}, you can safely ignore this email."]
    if welcome:
        intro = _welcome_intro(welcome)
        subject = f"Welcome to Delivery Sync - your verification code is {code}"
        body = "\n\n".join(intro) + f"\n\nTo finish setting up, verify your email with this code:\n\n    {code}\n\n" + "\n".join(tail) + f"\n\nLog in: {APP_URL}\n"
        _send_email(to_email, subject, body, html_body=_html_email(
            "Congratulations — your account is ready!", intro + ["To finish setting up, verify your email with this code:"],
            highlight=code, after=tail, button_label="Open Delivery Sync", button_url=APP_URL,
        ))
        return
    subject = f"Your Delivery Sync verification code: {code}"
    body = (
        f"Your Delivery Sync verification code is:\n\n    {code}\n\n"
        f"Enter it in the app to verify your email address. It expires in 10 minutes and works once.\n"
        f"Never share this code with anyone.\n\n"
        f"If you didn't create a Delivery Sync {who}, you can safely ignore this email."
    )
    _send_email(to_email, subject, body, html_body=_html_email(
        "Verify your email address",
        ["Enter this code in the app to verify your email address:"],
        highlight=code,
        after=tail,
    ))


def send_verification_email(to_email: str, code: str, welcome: dict | None = None) -> None:
    """Staff: emails the 6-digit verification code (OTP); at signup `welcome` adds the congratulations text."""
    _send_verification_code(to_email, code, "account", welcome)


def send_customer_verification_email(to_email: str, code: str, welcome: dict | None = None) -> None:
    """Customer: emails the 6-digit verification code (OTP); at signup `welcome` adds the congratulations text."""
    _send_verification_code(to_email, code, "account", welcome)


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
    """Stand-alone congratulations for a brand-new organization's admin. (Email/password signups get
    this text inside the verification email instead; this one is used for Google sign-ups.)"""
    subject = f"Welcome to Delivery Sync - {org_name} is ready"
    intro = _welcome_intro({"name": display_name, "org_name": org_name, "is_new_org": True, "invite_code": invite_code})
    _send_email(to_email, subject, "\n\n".join(intro) + f"\n\nLog in: {APP_URL}\n", html_body=_html_email(
        "Congratulations — your account is ready!", intro, button_label="Open Delivery Sync", button_url=APP_URL,
    ))


def send_staff_welcome_email(to_email: str, display_name: str, org_name: str, role: str) -> None:
    """Stand-alone congratulations for an agent/dispatcher who joined via invite code (Google sign-ups)."""
    subject = f"Welcome to {org_name} on Delivery Sync"
    intro = _welcome_intro({"name": display_name, "org_name": org_name, "is_new_org": False, "role": role})
    _send_email(to_email, subject, "\n\n".join(intro) + f"\n\nLog in: {APP_URL}\n", html_body=_html_email(
        "Congratulations — welcome aboard!", intro, button_label="Log in to Delivery Sync", button_url=APP_URL,
    ))


def send_customer_welcome_email(to_email: str, display_name: str) -> None:
    """Stand-alone congratulations for a new customer (Google sign-ups; email signups get it inside the verification email)."""
    subject = "Welcome to Delivery Sync - your account is ready"
    intro = _welcome_intro({"name": display_name})
    _send_email(to_email, subject, "\n\n".join(intro) + f"\n\nOpen Delivery Sync: {APP_URL}\n", html_body=_html_email(
        "Congratulations — your account is ready!", intro, button_label="Open Delivery Sync", button_url=APP_URL,
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


def _build_message(to_email: str, subject: str, body: str, html_body: str | None = None):
    """Builds the MIME message with the headers mail providers look at when deciding
    inbox vs spam: a real sender NAME, Date, a Message-ID on the sender's own domain,
    Reply-To, and the standard "automated transactional mail" marker (RFC 3834)."""
    if html_body:
        message = MIMEMultipart("alternative")
        message.attach(MIMEText(body, "plain", "utf-8"))
        message.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        message = MIMEText(body, "plain", "utf-8")
    sender_domain = FROM_EMAIL.rsplit("@", 1)[-1] if "@" in FROM_EMAIL else None
    message["Subject"] = subject
    message["From"] = formataddr((FROM_NAME, FROM_EMAIL))
    message["To"] = to_email
    message["Reply-To"] = FROM_EMAIL
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=sender_domain)
    message["Auto-Submitted"] = "auto-generated"          # this is a system message, not a person writing
    message["X-Auto-Response-Suppress"] = "OOF, AutoReply"  # don't let out-of-office bots reply to it
    return message


def _send_email(to_email: str, subject: str, body: str, html_body: str | None = None) -> None:
    if not SMTP_HOST:
        # No SMTP configured — this is the default, zero-cost path.
        print("=" * 60)
        print("EMAIL (no SMTP_HOST configured — printed instead of sent)")
        print(f"To: {to_email}")
        print(f"Subject: {subject}")
        print(body)
        print("=" * 60)
        # Honest accounting: nothing was delivered to an inbox.
        monitoring_svc.record_notification_sent(
            "email", success=False,
            error="SMTP_HOST isn't set, so emails are only printed in the backend console. Set SMTP_* in backend/.env and restart.",
        )
        return

    # SMTP_HOST is configured — actually send a real email (multipart/alternative when
    # there's an HTML version: plain text first, HTML preferred).
    message = _build_message(to_email, subject, body, html_body)

    def _attempt() -> None:
        # Port 465 = implicit TLS; anything else (587) = STARTTLS. The
        # timeout stops a slow/unreachable SMTP server hanging the request.
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
            server.starttls()
        with server:
            if SMTP_DEBUG:
                server.set_debuglevel(1)
            if SMTP_USERNAME and SMTP_PASSWORD:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            refused = server.sendmail(FROM_EMAIL, [to_email], message.as_string())
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)

    try:
        try:
            _attempt()
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, smtplib.SMTPResponseException) as transient:
            # 4xx = "try again later" (Gmail does this when logins come in rapid succession).
            # Retry ONCE after a short pause; permanent errors (auth, bad recipient: 5xx) fail straight away.
            code = getattr(transient, "smtp_code", 421)
            if isinstance(transient, smtplib.SMTPResponseException) and not (400 <= code < 500):
                raise
            time.sleep(2)
            _attempt()
        monitoring_svc.record_notification_sent("email", success=True)
    except Exception as error:
        monitoring_svc.record_notification_sent(
            "email", success=False, error=f"{type(error).__name__}: {error} — {_smtp_hint(error)}",
        )
        print(f"EMAIL SEND FAILED to {to_email}: {type(error).__name__}: {error}")
        print(f"  Hint: {_smtp_hint(error)}")
        raise


def _smtp_hint(error: Exception) -> str:
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return ("Login was rejected. For Gmail you must use a 16-letter App Password "
                "(Google Account > Security > 2-Step Verification > App passwords), not your normal password, "
                "and SMTP_USERNAME must be that same Gmail address.")
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return ("Couldn't reach the SMTP server. Check SMTP_HOST/SMTP_PORT (587 or 465), your internet "
                "connection, and that a firewall/antivirus isn't blocking outgoing SMTP.")
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "The recipient address was refused by the server."
    if isinstance(error, smtplib.SMTPSenderRefused):
        return "The From address was refused. Set FROM_EMAIL to the same address as SMTP_USERNAME."
    return "Run `python send_test_email.py you@example.com --debug` in the backend folder to see the full SMTP conversation."
