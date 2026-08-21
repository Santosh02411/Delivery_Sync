"""
Auth routes: signup and login. Also exposes `get_current_user`, a FastAPI
dependency that other routes use to identify who's making a request,
check their role, and check their organization — this is what makes both
role-based access AND multi-tenant isolation possible.
"""

import secrets
import string

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.user import (
    UserDB,
    UserSignup,
    UserLogin,
    UserOut,
    UserProfileUpdate,
    UserPasswordChange,
    TokenResponse,
    LoginResult,
    TwoFactorSetupOut,
    TwoFactorCodeRequest,
    TwoFactorLoginVerify,
    TwoFactorDisableRequest,
    TwoFactorStatusOut,
    TwoFactorEmailCodeRequest,
)
from app.models.organization import OrganizationDB
from app.models.password_reset import PasswordResetTokenDB, ForgotPasswordRequest, ResetPasswordRequest
from app.models.email_otp import EmailOtpDB, EMAIL_OTP_EXPIRY_MINUTES, generate_numeric_code, mask_email
from app.services.auth import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    create_two_factor_challenge_token,
)
from app.services.totp import generate_secret, get_provisioning_uri, verify_code
from app.services.rate_limiter import limiter
from app.services.email import send_password_reset_email, send_two_factor_code_email
from datetime import datetime, timedelta
import os

router = APIRouter(prefix="/auth", tags=["auth"])

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")


def generate_invite_code() -> str:
    """8-character, easy-to-read invite code (uppercase letters + digits)."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def _issue_and_send_email_otp(db: Session, user: UserDB, purpose: str) -> str:
    """
    Generates a fresh 6-digit code, stores its hash (see
    models/email_otp.py for why hashed, not plaintext), emails the plain
    code to the user, and returns the masked email address for display.
    Shared by both the "confirm your inbox to enable email 2FA" flow and
    the "here's your login code" flow — same mechanics, different
    `purpose` label so the two can't be swapped for each other.
    """
    code = generate_numeric_code()
    otp = EmailOtpDB(user_id=user.id, code_hash=hash_password(code), purpose=purpose)
    db.add(otp)
    db.commit()

    send_two_factor_code_email(user.email, code, purpose)
    return mask_email(user.email)


def _verify_and_consume_email_otp(db: Session, user_id: str, purpose: str, code: str) -> bool:
    """
    Checks `code` against the most recent not-yet-used, not-yet-expired
    OTP of this purpose for this user, and marks it used on success —
    each code works exactly once, and only the most recently issued one
    is ever accepted (so requesting a resend invalidates checking an
    older email still sitting in the inbox, avoiding any ambiguity about
    which one is "the real code").
    """
    otp = (
        db.query(EmailOtpDB)
        .filter(EmailOtpDB.user_id == user_id, EmailOtpDB.purpose == purpose, EmailOtpDB.used == False)  # noqa: E712
        .order_by(EmailOtpDB.created_at.desc())
        .first()
    )
    if not otp or otp.expires_at < datetime.utcnow():
        return False
    if not verify_password(code, otp.code_hash):
        return False

    otp.used = True
    db.commit()
    return True


@router.post("/signup", response_model=TokenResponse)
@limiter.limit("5/minute")
def signup(request: Request, payload: UserSignup, db: Session = Depends(get_db)):
    existing = db.query(UserDB).filter(UserDB.username == payload.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="That username's already taken. Try another.")

    existing_email = db.query(UserDB).filter(UserDB.email == payload.email).first()
    if existing_email:
        raise HTTPException(status_code=400, detail="That email is already registered. Try logging in instead.")

    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

    if not payload.org_name and not payload.invite_code:
        raise HTTPException(
            status_code=400,
            detail="Provide either an organization name (to create one) or an invite code (to join one).",
        )
    if payload.org_name and payload.invite_code:
        raise HTTPException(
            status_code=400,
            detail="Provide only ONE of organization name or invite code, not both.",
        )

    new_org_invite_code = None

    if payload.org_name:
        # Creating a brand new organization — this user becomes its admin
        # automatically, regardless of which role they selected, since
        # someone has to be able to manage the org from the very start.
        org = OrganizationDB(name=payload.org_name.strip(), invite_code=generate_invite_code())
        db.add(org)
        db.commit()
        db.refresh(org)
        org_id = org.id
        effective_role = "admin"
        new_org_invite_code = org.invite_code
    else:
        # Joining an existing organization via invite code
        org = db.query(OrganizationDB).filter(OrganizationDB.invite_code == payload.invite_code).first()
        if not org:
            raise HTTPException(status_code=400, detail="That invite code doesn't match any organization.")

        # SECURITY: anyone joining via invite code must never be able to
        # self-select the "admin" role. An invite code is meant for
        # regular team members (agents/dispatchers) to join an existing
        # organization — without this check, anyone holding a valid
        # invite code could simply choose "admin" on this form and gain
        # full administrative control over someone else's organization
        # (deactivating users, resetting passwords, viewing everything).
        # Admin status is only ever granted automatically to whoever
        # creates the organization in the first place (the branch above).
        if payload.role.value == "admin":
            raise HTTPException(
                status_code=400,
                detail="You can't self-assign the admin role when joining an existing organization.",
            )

        org_id = org.id
        effective_role = payload.role.value

    user = UserDB(
        username=payload.username,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=effective_role,
        display_name=payload.display_name,
        org_id=org_id,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": user.id, "role": user.role.value, "org_id": user.org_id})
    return {"access_token": token, "user": user, "org_invite_code": new_org_invite_code}


@router.post("/login", response_model=LoginResult)
@limiter.limit("10/minute")
def login(request: Request, payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.username == payload.username).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect username or password.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been deactivated. Contact your admin.")

    if user.totp_enabled:
        # Password alone isn't enough for this account — hand back a
        # short-lived challenge token instead of a real session, and let
        # the frontend prompt for the code next. For the "email" method
        # there's no code sitting in an app yet, so send one right now;
        # for "totp" the code already exists in the user's authenticator
        # app, nothing to send.
        challenge_token = create_two_factor_challenge_token(user.id)
        masked_email = None
        if user.two_factor_method == "email":
            masked_email = _issue_and_send_email_otp(db, user, purpose="login")
        return {
            "requires_2fa": True,
            "challenge_token": challenge_token,
            "two_factor_method": user.two_factor_method,
            "masked_email": masked_email,
        }

    token = create_access_token({"sub": user.id, "role": user.role.value, "org_id": user.org_id})
    return {"access_token": token, "user": user, "org_invite_code": None}


@router.post("/2fa/resend-code")
@limiter.limit("5/minute")
def resend_two_factor_login_code(request: Request, payload: TwoFactorLoginVerify, db: Session = Depends(get_db)):
    """
    Sends a fresh login code to an "email" 2FA account's inbox — for
    when the first one expired, got lost, or landed in spam.
    `payload.code` is ignored here (the model is reused for its
    `challenge_token` field); only the token matters.
    """
    decoded = decode_access_token(payload.challenge_token)
    if not decoded or "pending_2fa_user_id" not in decoded:
        raise HTTPException(status_code=401, detail="This login attempt has expired. Log in again.")

    user = db.query(UserDB).filter(UserDB.id == decoded["pending_2fa_user_id"]).first()
    if not user or not user.is_active or not user.totp_enabled:
        raise HTTPException(status_code=401, detail="This login attempt has expired. Log in again.")
    if user.two_factor_method != "email":
        raise HTTPException(status_code=400, detail="This account doesn't use email codes.")

    masked_email = _issue_and_send_email_otp(db, user, purpose="login")
    return {"sent": True, "masked_email": masked_email}


@router.post("/2fa/verify-login", response_model=TokenResponse)
@limiter.limit("10/minute")
def verify_two_factor_login(request: Request, payload: TwoFactorLoginVerify, db: Session = Depends(get_db)):
    """
    Second step of login for an account with 2FA turned on: exchanges a
    valid challenge_token (from POST /auth/login) plus a correct code
    for a real access token. Branches on the account's
    `two_factor_method` to check against the right kind of code.
    """
    decoded = decode_access_token(payload.challenge_token)
    if not decoded or "pending_2fa_user_id" not in decoded:
        raise HTTPException(status_code=401, detail="This login attempt has expired. Log in again.")

    user = db.query(UserDB).filter(UserDB.id == decoded["pending_2fa_user_id"]).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="This login attempt has expired. Log in again.")

    if not user.totp_enabled:
        # 2FA was turned off between step 1 and step 2 (rare, but
        # possible) — nothing left to verify against.
        raise HTTPException(status_code=400, detail="Two-factor authentication is no longer enabled on this account.")

    if user.two_factor_method == "email":
        if not _verify_and_consume_email_otp(db, user.id, purpose="login", code=payload.code.strip()):
            raise HTTPException(status_code=401, detail="Incorrect or expired code. Request a new one and try again.")
    else:
        if not user.totp_secret or not verify_code(user.totp_secret, payload.code):
            raise HTTPException(status_code=401, detail="Incorrect code. Check your authenticator app and try again.")

    token = create_access_token({"sub": user.id, "role": user.role.value, "org_id": user.org_id})
    return {"access_token": token, "user": user, "org_invite_code": None}


@router.post("/forgot-password")
@limiter.limit("3/minute")
def forgot_password(request: Request, payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Requests a password reset email. ALWAYS returns the same generic
    success message, whether or not that email actually belongs to an
    account — revealing "yes, that email exists" vs "no, it doesn't"
    would let anyone probe which emails are registered users, which is a
    real (if minor) information leak. The rate limit here (3/minute) also
    specifically guards against someone hammering this endpoint to spam
    reset emails at a real user's inbox.
    """
    GENERIC_RESPONSE = {
        "message": "If that email is registered, a password reset link has been sent."
    }

    user = db.query(UserDB).filter(UserDB.email == payload.email).first()
    if not user or not user.is_active:
        return GENERIC_RESPONSE

    reset_token = PasswordResetTokenDB(user_id=user.id)
    db.add(reset_token)
    db.commit()
    db.refresh(reset_token)

    reset_link = f"{FRONTEND_URL}/?reset_token={reset_token.token}"
    send_password_reset_email(user.email, reset_link)

    return GENERIC_RESPONSE


@router.post("/reset-password")
@limiter.limit("5/minute")
def reset_password(request: Request, payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Completes a password reset, given a valid, unused, unexpired token."""
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

    reset_token = db.query(PasswordResetTokenDB).filter(
        PasswordResetTokenDB.token == payload.token
    ).first()

    if not reset_token:
        raise HTTPException(status_code=400, detail="This reset link is invalid.")
    if reset_token.used:
        raise HTTPException(status_code=400, detail="This reset link has already been used.")
    if reset_token.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="This reset link has expired. Request a new one.")

    user = db.query(UserDB).filter(UserDB.id == reset_token.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="This reset link is invalid.")

    user.hashed_password = hash_password(payload.new_password)
    reset_token.used = True
    db.commit()

    return {"message": "Password reset successfully. You can now log in with your new password."}


def get_current_user(
    authorization: str = Header(None), db: Session = Depends(get_db)
) -> UserDB:
    """
    FastAPI dependency: extracts and validates the JWT from the
    'Authorization: Bearer <token>' header, and returns the corresponding
    user. Any route that depends on this requires a logged-in user with
    an active account.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Your session expired. Log in again.")

    token = authorization.split(" ")[1]
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Your session expired. Log in again.")

    user = db.query(UserDB).filter(UserDB.id == payload.get("sub")).first()
    if not user:
        raise HTTPException(status_code=401, detail="Your session expired. Log in again.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been deactivated. Contact your admin.")

    return user


# ---------- Self-service account settings (staff, logged in) ----------
# Mirrors routes/customer_auth.py's GET/PATCH /me + POST /me/change-password
# exactly, for the same self-service reasons — a staff member editing
# their own display name or changing their own password shouldn't need
# an admin to do it for them (admin.py's reset-password endpoint is for
# an admin acting on SOMEONE ELSE's account; this is a user acting on
# their own, and requires proving they know the current password rather
# than an admin's authority to skip that check).

@router.get("/me", response_model=UserOut)
def get_my_profile(current_user: UserDB = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserOut)
def update_my_profile(
    payload: UserProfileUpdate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    if payload.display_name is not None:
        display_name = payload.display_name.strip()
        if not display_name:
            raise HTTPException(status_code=400, detail="Display name can't be empty.")
        current_user.display_name = display_name

    if payload.email is not None:
        email = payload.email.strip()
        if not email:
            raise HTTPException(status_code=400, detail="Email can't be empty.")
        if email != current_user.email:
            existing = db.query(UserDB).filter(UserDB.email == email).first()
            if existing:
                raise HTTPException(status_code=400, detail="Another account already uses that email.")
            current_user.email = email

    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/me/change-password")
def change_my_password(
    payload: UserPasswordChange,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters.")

    current_user.hashed_password = hash_password(payload.new_password)
    db.commit()
    return {"message": "Password changed."}


# ---------- Two-factor auth: setup / enable / disable (staff, logged in) ----------
# These three routes require an already-valid access token, which is why
# they're defined after get_current_user above rather than next to
# /login and /2fa/verify-login (both of which run BEFORE a session
# exists). Turning 2FA on is a two-step process on purpose — /2fa/setup
# hands back a QR code but does NOT enable anything yet, and /2fa/enable
# only flips it on once the user proves their authenticator app actually
# received it by submitting one real code. Skipping straight to "on"
# would risk locking someone out on a secret their app never scanned.

@router.get("/2fa/status", response_model=TwoFactorStatusOut)
def get_two_factor_status(current_user: UserDB = Depends(get_current_user)):
    return {"totp_enabled": current_user.totp_enabled, "two_factor_method": current_user.two_factor_method}


@router.post("/2fa/setup", response_model=TwoFactorSetupOut)
def setup_two_factor(db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    """Start setup for the AUTHENTICATOR APP method. For the email-code method, see /2fa/setup-email below instead."""
    if current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is already enabled. Disable it first to set up a new device.")

    secret = generate_secret()
    current_user.totp_secret = secret
    db.commit()

    uri = get_provisioning_uri(secret, current_user.username)
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/2fa/enable")
def enable_two_factor(
    payload: TwoFactorCodeRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Confirms the AUTHENTICATOR APP method with one real code from it, and turns 2FA on."""
    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="Start setup first (POST /auth/2fa/setup) before confirming a code.")
    if current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is already enabled.")

    if not verify_code(current_user.totp_secret, payload.code):
        raise HTTPException(status_code=400, detail="Incorrect code. Check your authenticator app and try again.")

    current_user.totp_enabled = True
    current_user.two_factor_method = "totp"
    db.commit()
    return {"success": True, "message": "Two-factor authentication is now enabled on your account."}


@router.post("/2fa/setup-email")
@limiter.limit("5/minute")
def setup_email_two_factor(request: Request, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    """
    Start setup for the EMAIL CODE method: sends a confirmation code to
    the user's own account email right away — the "device" being set up
    here is the inbox itself, so there's nothing to scan first the way
    there is with an authenticator app. POST /2fa/enable-email with that
    code turns it on.
    """
    if current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is already enabled. Disable it first to switch methods.")

    masked_email = _issue_and_send_email_otp(db, current_user, purpose="enable_2fa")
    return {"sent": True, "masked_email": masked_email}


@router.post("/2fa/enable-email")
def enable_email_two_factor(
    payload: TwoFactorEmailCodeRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    if current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is already enabled.")

    if not _verify_and_consume_email_otp(db, current_user.id, purpose="enable_2fa", code=payload.code.strip()):
        raise HTTPException(status_code=400, detail="Incorrect or expired code. Request a new one and try again.")

    current_user.totp_enabled = True
    current_user.two_factor_method = "email"
    current_user.totp_secret = None  # this account uses email codes, not an authenticator secret
    db.commit()
    return {"success": True, "message": "Two-factor authentication is now enabled on your account."}


@router.post("/2fa/disable")
def disable_two_factor(
    payload: TwoFactorDisableRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Requires the account password again — not just an active session —
    so someone who grabs an unlocked, logged-in device can't silently
    strip 2FA off the account themselves. Works the same regardless of
    which method (authenticator app or email) is currently active."""
    if not verify_password(payload.password, current_user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect password.")

    current_user.totp_enabled = False
    current_user.totp_secret = None
    current_user.two_factor_method = "totp"
    db.commit()
    return {"success": True, "message": "Two-factor authentication has been disabled."}
