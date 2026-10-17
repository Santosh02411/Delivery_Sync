"""
Email verification by one-time code (OTP) — for BOTH staff (UserDB) and
customers (CustomerDB).

Replaces the old "click the link" flow: a 6-digit code is emailed, the
person types it into the app. Why this is safer/simpler than a link:
nothing to click in an email (no phishing-style links to train people on),
works when the email is opened on a different device than the app, and
the code is useless without the logged-in session it must be entered in.

Storage reuses EmailOtpDB (hashed code, 10-minute expiry, single use,
only the latest code is valid). `purpose` keeps staff and customer codes
(and the 2FA login codes that share the table) from being swapped.
Wrong guesses are counted; after MAX_OTP_ATTEMPTS the code is burned and a
new one must be requested, so the 1-in-a-million code space can't be
brute-forced inside its lifetime.
"""

import secrets
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.email_otp import EmailOtpDB, EMAIL_OTP_EXPIRY_MINUTES
from app.services.auth import hash_password, verify_password

STAFF_PURPOSE = "verify_email"
CUSTOMER_PURPOSE = "customer_verify_email"
MAX_OTP_ATTEMPTS = 5


class VerificationCodeError(ValueError):
    """The code was wrong/expired/used up. str(e) is safe to show the user."""


def issue_code(db: Session, subject_id: str, purpose: str) -> str:
    """Creates a fresh code, invalidating any earlier unused one, and returns the plain code (to be emailed, never stored)."""
    db.query(EmailOtpDB).filter(
        EmailOtpDB.user_id == subject_id, EmailOtpDB.purpose == purpose, EmailOtpDB.used == False  # noqa: E712
    ).update({"used": True})
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(EmailOtpDB(user_id=subject_id, code_hash=hash_password(code), purpose=purpose))
    db.commit()
    return code


def check_code(db: Session, subject_id: str, purpose: str, code: str) -> None:
    """Returns None if `code` is the current valid code (and consumes it); raises VerificationCodeError otherwise."""
    otp = (
        db.query(EmailOtpDB)
        .filter(EmailOtpDB.user_id == subject_id, EmailOtpDB.purpose == purpose, EmailOtpDB.used == False)  # noqa: E712
        .order_by(EmailOtpDB.created_at.desc())
        .first()
    )
    if not otp:
        raise VerificationCodeError("No active code. Request a new one.")
    if otp.expires_at < datetime.utcnow():
        raise VerificationCodeError(f"That code expired (codes last {EMAIL_OTP_EXPIRY_MINUTES} minutes). Request a new one.")

    if not verify_password(code.strip(), otp.code_hash):
        otp.attempts = (otp.attempts or 0) + 1
        remaining = MAX_OTP_ATTEMPTS - otp.attempts
        if remaining <= 0:
            otp.used = True
            db.commit()
            raise VerificationCodeError("Too many wrong attempts. Request a new code.")
        db.commit()
        raise VerificationCodeError(f"Incorrect code. {remaining} attempt{'s' if remaining != 1 else ''} left.")

    otp.used = True
    db.commit()
