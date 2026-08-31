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
from app.models.email_verification import EmailVerificationTokenDB, VerifyEmailRequest, VERIFICATION_TOKEN_EXPIRY_HOURS
from app.models.refresh_token import RefreshTokenDB, RefreshTokenRequest, RefreshTokenResponse
from app.services.auth import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    create_two_factor_challenge_token,
    generate_refresh_token,
    hash_refresh_token,
)
from app.services.totp import generate_secret, get_provisioning_uri, verify_code
from app.services.rate_limiter import limiter
from app.services.email import send_password_reset_email, send_two_factor_code_email, send_verification_email
from app.services.captcha import verify_captcha, IS_CONFIGURED as CAPTCHA_CONFIGURED
from datetime import datetime, timedelta
import os

router = APIRouter(prefix="/auth", tags=["auth"])

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")


def _issue_refresh_token(db: Session, user_id: str) -> str:
    """
    Creates a new refresh token row (hash stored, not the raw value) and
    returns the RAW token — the only place the raw value ever exists is
    this return value and whatever the caller does with it immediately
    (put it in a response body). It is never logged or stored anywhere
    in plaintext.
    """
    raw_token = generate_refresh_token()
    db.add(RefreshTokenDB(user_id=user_id, token_hash=hash_refresh_token(raw_token)))
    db.commit()
    return raw_token


def _issue_and_send_verification_email(db: Session, user: UserDB) -> None:
    verification = EmailVerificationTokenDB(user_id=user.id)
    db.add(verification)
    db.commit()
    db.refresh(verification)
    verify_link = f"{FRONTEND_URL}/?verify_email_token={verification.token}"
    send_verification_email(user.email, verify_link)


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
    if CAPTCHA_CONFIGURED and not verify_captcha(payload.captcha_token):
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed. Please try again.")

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
        if org.is_suspended:
            raise HTTPException(status_code=403, detail="This organization is currently suspended and isn't accepting new members.")

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

    _issue_and_send_verification_email(db, user)

    token = create_access_token({"sub": user.id, "role": user.role.value, "org_id": user.org_id})
    refresh_token = _issue_refresh_token(db, user.id)
    return {"access_token": token, "refresh_token": refresh_token, "user": user, "org_invite_code": new_org_invite_code}


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
    refresh_token = _issue_refresh_token(db, user.id)
    return {"access_token": token, "refresh_token": refresh_token, "user": user, "org_invite_code": None}


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
    refresh_token = _issue_refresh_token(db, user.id)
    return {"access_token": token, "refresh_token": refresh_token, "user": user, "org_invite_code": None}


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

    if CAPTCHA_CONFIGURED and not verify_captcha(payload.captcha_token):
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed. Please try again.")

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


# ---------- Email verification ----------
# Confirms the email address on a staff account is real/reachable.
# Deliberately does NOT block login or any other action — this project
# treats it as informational rather than a hard gate, for two reasons:
# (1) an admin creating an org and inviting teammates needs to be able
# to use the account immediately, not wait on an email round-trip
# before they can even see their own dashboard; (2) SMTP is optional in
# this project (see backend/.env.example) — locking accounts out until
# a verification email arrives would make the app partially unusable
# for anyone running it without SMTP configured. What this DOES give:
# a real, checkable `email_verified` flag or the frontend to show a
# "please verify" nudge, and a genuine confirmation step for anyone who
# wants one — the same trade-off many real products make (Slack,
# GitHub, etc. all let you use the product before verifying).

@router.post("/verify-email")
def verify_email(payload: VerifyEmailRequest, db: Session = Depends(get_db)):
    verification = db.query(EmailVerificationTokenDB).filter(
        EmailVerificationTokenDB.token == payload.token
    ).first()

    if not verification:
        raise HTTPException(status_code=400, detail="This verification link is invalid.")
    if verification.used:
        raise HTTPException(status_code=400, detail="This verification link has already been used.")
    if verification.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="This verification link has expired. Request a new one.")

    user = db.query(UserDB).filter(UserDB.id == verification.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="This verification link is invalid.")

    user.email_verified = True
    verification.used = True
    db.commit()

    return {"message": "Email verified."}


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


@router.post("/resend-verification")
@limiter.limit("3/minute")
def resend_verification(request: Request, current_user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.email_verified:
        return {"message": "This account's email is already verified."}
    _issue_and_send_verification_email(db, current_user)
    return {"message": "Verification email sent."}


# ---------- Refresh tokens ----------
# See models/refresh_token.py's module docstring for the full design
# rationale (hashing, rotation, theft detection). This is what a client
# calls with the refresh_token it got at login/signup, shortly before
# its short-lived access token (30 minutes — services/auth.py) expires,
# to get a new pair of both without making the person log in again.

@router.post("/refresh", response_model=RefreshTokenResponse)
@limiter.limit("30/minute")
def refresh_access_token(request: Request, payload: RefreshTokenRequest, db: Session = Depends(get_db)):
    token_hash = hash_refresh_token(payload.refresh_token)
    stored = db.query(RefreshTokenDB).filter(RefreshTokenDB.token_hash == token_hash).first()

    if not stored:
        raise HTTPException(status_code=401, detail="Invalid refresh token. Log in again.")

    if stored.used or stored.revoked_at is not None:
        # Reuse of an already-rotated (or already-revoked) refresh
        # token — see models/refresh_token.py's docstring: this is a
        # theft signal, not an ordinary expiry. Revoke every other
        # still-live token for this user too, since at this point
        # neither the legitimate holder's copy nor whoever just
        # presented this one can be trusted to be the only party who
        # has it.
        db.query(RefreshTokenDB).filter(
            RefreshTokenDB.user_id == stored.user_id,
            RefreshTokenDB.revoked_at.is_(None),
        ).update({"revoked_at": datetime.utcnow()})
        db.commit()
        raise HTTPException(status_code=401, detail="This session was revoked. Log in again.")

    if stored.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Your session has expired. Log in again.")

    user = db.query(UserDB).filter(UserDB.id == stored.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid refresh token. Log in again.")

    new_raw_token = generate_refresh_token()
    new_row = RefreshTokenDB(user_id=user.id, token_hash=hash_refresh_token(new_raw_token))
    db.add(new_row)
    db.flush()  # assigns new_row.id without a second round trip

    stored.used = True
    stored.replaced_by_id = new_row.id
    db.commit()

    new_access_token = create_access_token({"sub": user.id, "role": user.role.value, "org_id": user.org_id})
    return {"access_token": new_access_token, "refresh_token": new_raw_token}


@router.post("/logout")
def logout(payload: RefreshTokenRequest, db: Session = Depends(get_db)):
    """
    Server-side session revocation — this is the actual "log out"
    beyond just the frontend clearing its local storage. Without this,
    a refresh token issued at login would stay silently valid for its
    full expiry (30 days) even after the person clicked "log out,"
    since a JWT-only scheme has no way to invalidate anything before
    its own expiry. Deliberately not authenticated with an access
    token: logging out should work even if the access token already
    expired, and the refresh token itself is enough proof of the
    session being ended.
    """
    token_hash = hash_refresh_token(payload.refresh_token)
    stored = db.query(RefreshTokenDB).filter(RefreshTokenDB.token_hash == token_hash).first()
    if stored and stored.revoked_at is None:
        stored.revoked_at = datetime.utcnow()
        db.commit()
    return {"message": "Logged out."}


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
