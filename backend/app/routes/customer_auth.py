"""
Customer authentication — separate signup/login from the staff
(agent/dispatcher/admin) system in routes/auth.py. Reuses the same
password hashing and JWT helpers from services/auth.py, but issues
tokens with a distinct payload shape ({"customer_id": ...} instead of
{"sub": ..., "role": ...}) so a customer token and a staff token can
never be confused with each other, even accidentally.
"""

import os
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.customer import (
    CustomerDB, CustomerSignup, CustomerLogin, CustomerTokenResponse, CustomerOut, CustomerProfileUpdate,
    CustomerPasswordChange, CustomerSetPassword, CustomerLoginResult, CustomerTwoFactorCode,
    CustomerTwoFactorLoginVerify, CustomerTwoFactorDisable,
)
from app.models.customer_password_reset import CustomerPasswordResetTokenDB, CustomerForgotPasswordRequest, CustomerResetPasswordRequest
from app.models.customer_email_verification import CustomerVerifyEmailRequest
from app.services import email_verification as email_verification_svc
from app.models.customer_refresh_token import CustomerRefreshTokenDB, CustomerRefreshTokenRequest, CustomerRefreshTokenResponse
from app.models.delivery import DeliveryRecordDB
from app.services.auth import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
    create_customer_oauth_state_token,
    create_customer_oauth_login_code,
    create_customer_two_factor_challenge_token,
)
from app.services.email import (
    send_customer_password_reset_email, send_customer_verification_email, send_customer_welcome_email,
    send_two_factor_code_email, send_security_alert_email,
)
from app.services import security as security_svc
from app.services.totp import generate_secret, get_provisioning_uri, verify_code
from app.models.email_otp import mask_email
from app.services.captcha import verify_captcha, IS_CONFIGURED as CAPTCHA_CONFIGURED
from app.services.rate_limiter import limiter
from app.services import oauth as oauth_svc
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

router = APIRouter(prefix="/customer", tags=["customer-auth"])

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3700")


def _issue_customer_refresh_token(db: Session, customer_id: str) -> str:
    raw_token = generate_refresh_token()
    db.add(CustomerRefreshTokenDB(customer_id=customer_id, token_hash=hash_refresh_token(raw_token)))
    db.commit()
    return raw_token


def _send_customer_welcome(customer: CustomerDB) -> None:
    """Congratulations email for a brand-new customer account. Best-effort
    (same reasoning as staff welcome emails in routes/auth.py): an SMTP
    hiccup must never stop someone from creating an account."""
    try:
        send_customer_welcome_email(customer.email, customer.name or customer.email)
    except Exception as error:  # noqa: BLE001
        print(f"Welcome email failed for {customer.email}: {error}")


def _issue_and_send_customer_verification_email(db: Session, customer: CustomerDB, welcome: dict | None = None) -> None:
    """Emails a fresh 6-digit verification code (see services/email_verification.py).
    At signup, `welcome` makes it the combined "Congratulations + your code" email."""
    code = email_verification_svc.issue_code(db, customer.id, email_verification_svc.CUSTOMER_PURPOSE)
    send_customer_verification_email(customer.email, code, welcome=welcome)


CUSTOMER_2FA_ENABLE_PURPOSE = "customer_2fa_enable"
CUSTOMER_2FA_LOGIN_PURPOSE = "customer_2fa_login"


def _send_customer_email_2fa_code(db: Session, customer: CustomerDB, kind: str) -> str:
    """Emails a fresh 6-digit 2FA code (kind: 'enable' or 'login'); returns the masked address."""
    purpose = CUSTOMER_2FA_ENABLE_PURPOSE if kind == "enable" else CUSTOMER_2FA_LOGIN_PURPOSE
    code = email_verification_svc.issue_code(db, customer.id, purpose)
    send_two_factor_code_email(customer.email, code, "enable_2fa" if kind == "enable" else "login")
    return mask_email(customer.email)


def _customer_from_challenge(db: Session, challenge_token: str) -> CustomerDB:
    decoded = decode_access_token(challenge_token)
    if not decoded or "pending_2fa_customer_id" not in decoded:
        raise HTTPException(status_code=401, detail="This login attempt has expired. Log in again.")
    customer = db.query(CustomerDB).filter(CustomerDB.id == decoded["pending_2fa_customer_id"]).first()
    if not customer or not customer.totp_enabled:
        raise HTTPException(status_code=401, detail="This login attempt has expired. Log in again.")
    return customer


@router.post("/signup", response_model=CustomerTokenResponse)
@limiter.limit("5/minute")
def customer_signup(request: Request, payload: CustomerSignup, db: Session = Depends(get_db)):
    if CAPTCHA_CONFIGURED and not verify_captcha(payload.captcha_token):
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed. Please try again.")

    existing = db.query(CustomerDB).filter(CustomerDB.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with that email already exists. Try logging in.")

    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

    customer = CustomerDB(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        name=payload.name,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)

    # Retroactively link any existing deliveries that used this email as
    # customer_email but were created before this account existed — so
    # signing up doesn't leave past orders stranded outside the dashboard.
    db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.customer_email == payload.email,
        DeliveryRecordDB.customer_id.is_(None),
    ).update({"customer_id": customer.id})
    db.commit()

    _issue_and_send_customer_verification_email(db, customer, welcome={"name": customer.name or customer.email})

    token = create_access_token({"customer_id": customer.id})
    refresh_token = _issue_customer_refresh_token(db, customer.id)
    return {"access_token": token, "refresh_token": refresh_token, "customer": customer}


@router.post("/login", response_model=CustomerLoginResult)
@limiter.limit("10/minute")
def customer_login(request: Request, payload: CustomerLogin, db: Session = Depends(get_db)):
    customer = db.query(CustomerDB).filter(CustomerDB.email == payload.email).first()
    if not customer or not verify_password(payload.password, customer.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    if customer.totp_enabled:
        # Password alone isn't enough: hand back a short-lived challenge instead of a
        # session. For the email method, send the code now; for the authenticator
        # method the code already lives in their app.
        masked = None
        if customer.two_factor_method == "email":
            masked = _send_customer_email_2fa_code(db, customer, "login")
        return {
            "requires_2fa": True,
            "challenge_token": create_customer_two_factor_challenge_token(customer.id),
            "two_factor_method": customer.two_factor_method,
            "masked_email": masked,
        }

    token = create_access_token({"customer_id": customer.id})
    refresh_token = _issue_customer_refresh_token(db, customer.id)
    return {"access_token": token, "refresh_token": refresh_token, "customer": customer}


class CustomerOAuthAuthorizationUrlOut(BaseModel):
    authorization_url: str


class CustomerOAuthExchangeRequest(BaseModel):
    code: str


@router.get("/oauth/google/login", response_model=CustomerOAuthAuthorizationUrlOut)
@limiter.limit("10/minute")
def start_customer_google_oauth_login(request: Request):
    """
    Customer-side equivalent of routes/auth.py's staff
    /oauth/google/login — see that route's docstring for the overall
    flow. Notably simpler here: no org_name/invite_code/role query
    params at all, since a customer account carries no org context
    (see CustomerDB's own comment on why).
    """
    if not oauth_svc.GOOGLE_OAUTH_CONFIGURED:
        raise HTTPException(
            status_code=400,
            detail="Google sign-in isn't configured for this deployment. Set GOOGLE_CLIENT_ID and "
                   "GOOGLE_CLIENT_SECRET to enable it.",
        )
    state = create_customer_oauth_state_token()
    return {"authorization_url": oauth_svc.build_authorization_url(state, redirect_uri=oauth_svc.GOOGLE_CUSTOMER_REDIRECT_URI)}


@router.get("/oauth/google/callback")
def customer_google_oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None, db: Session = Depends(get_db)):
    """
    Customer-side equivalent of routes/auth.py's staff
    /oauth/google/callback. Same "Google redirects the browser here
    directly, so every path ends in a redirect back to the frontend"
    shape — see that route's docstring for why. Also links to an
    existing password-based customer account by verified email, same
    trust bar as the staff flow, and retroactively links any past
    deliveries placed under this email before an account existed —
    mirroring what /customer/signup already does for a password
    signup, so an OAuth signup doesn't behave differently there.
    """
    def _redirect_with_error(message: str) -> RedirectResponse:
        from urllib.parse import quote
        return RedirectResponse(f"{FRONTEND_URL}/?customer_oauth_error={quote(message)}")

    if error:
        return _redirect_with_error("Google sign-in was cancelled or denied.")
    if not oauth_svc.GOOGLE_OAUTH_CONFIGURED:
        return _redirect_with_error("Google sign-in isn't configured for this deployment.")
    if not code or not state:
        return _redirect_with_error("Missing information from Google's redirect. Please try again.")

    decoded_state = decode_access_token(state)
    if not decoded_state or "customer_oauth_provider" not in decoded_state:
        return _redirect_with_error("This sign-in attempt has expired. Please try again.")

    try:
        profile = oauth_svc.exchange_code_for_profile(code, redirect_uri=oauth_svc.GOOGLE_CUSTOMER_REDIRECT_URI)
    except oauth_svc.GoogleOAuthError as err:
        return _redirect_with_error(str(err))

    customer = db.query(CustomerDB).filter(
        CustomerDB.oauth_provider == "google",
        CustomerDB.oauth_subject_id == profile["subject_id"],
    ).first()

    if not customer:
        customer = db.query(CustomerDB).filter(CustomerDB.email == profile["email"]).first()
        if customer:
            customer.oauth_provider = "google"
            customer.oauth_subject_id = profile["subject_id"]
            customer.email_verified = True
            db.commit()
            db.refresh(customer)

    if not customer:
        customer = CustomerDB(
            email=profile["email"],
            hashed_password=hash_password(secrets.token_urlsafe(32)),
            has_usable_password=False,
            name=profile["name"],
            email_verified=True,
            oauth_provider="google",
            oauth_subject_id=profile["subject_id"],
        )
        db.add(customer)
        db.commit()
        db.refresh(customer)

        # Same retroactive-linking behavior as POST /customer/signup —
        # see that route's own comment.
        db.query(DeliveryRecordDB).filter(
            DeliveryRecordDB.customer_email == profile["email"],
            DeliveryRecordDB.customer_id.is_(None),
        ).update({"customer_id": customer.id})
        db.commit()
        _send_customer_welcome(customer)

    login_code = create_customer_oauth_login_code(customer.id)
    from urllib.parse import quote
    return RedirectResponse(f"{FRONTEND_URL}/?customer_oauth_code={quote(login_code)}")


@router.post("/oauth/exchange", response_model=CustomerTokenResponse)
@limiter.limit("10/minute")
def exchange_customer_oauth_login_code(request: Request, payload: CustomerOAuthExchangeRequest, db: Session = Depends(get_db)):
    """
    Customer-side equivalent of routes/auth.py's staff
    /oauth/exchange — trades the one-time code from the callback
    redirect for a real access/refresh token pair.
    """
    decoded = decode_access_token(payload.code)
    if not decoded or "customer_oauth_login_customer_id" not in decoded:
        raise HTTPException(status_code=401, detail="This sign-in link has expired. Please sign in again.")

    customer = db.query(CustomerDB).filter(CustomerDB.id == decoded["customer_oauth_login_customer_id"]).first()
    if not customer:
        raise HTTPException(status_code=401, detail="This sign-in link has expired. Please sign in again.")

    token = create_access_token({"customer_id": customer.id})
    refresh_token = _issue_customer_refresh_token(db, customer.id)
    return {"access_token": token, "refresh_token": refresh_token, "customer": customer}


@router.post("/forgot-password")
@limiter.limit("3/minute")
def customer_forgot_password(request: Request, payload: CustomerForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Requests a password reset email for a customer account. Mirrors
    routes/auth.py's staff forgot-password endpoint exactly on purpose,
    including the same information-leak precaution: this ALWAYS returns
    the same generic message whether or not the email belongs to a real
    account, so this endpoint can't be used to probe which emails are
    registered customers. Same 3/minute rate limit for the same reason
    (stop someone from spamming reset emails at a real customer's inbox).
    """
    GENERIC_RESPONSE = {
        "message": "If that email is registered, a password reset link has been sent."
    }

    if CAPTCHA_CONFIGURED and not verify_captcha(payload.captcha_token):
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed. Please try again.")

    customer = db.query(CustomerDB).filter(CustomerDB.email == payload.email).first()
    if not customer:
        return GENERIC_RESPONSE

    reset_token = CustomerPasswordResetTokenDB(customer_id=customer.id)
    db.add(reset_token)
    db.commit()
    db.refresh(reset_token)

    reset_link = f"{FRONTEND_URL}/?customer_reset_token={reset_token.token}"
    send_customer_password_reset_email(customer.email, reset_link)

    return GENERIC_RESPONSE


@router.post("/reset-password")
@limiter.limit("5/minute")
def customer_reset_password(request: Request, payload: CustomerResetPasswordRequest, db: Session = Depends(get_db)):
    """Completes a customer password reset, given a valid, unused, unexpired token."""
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

    reset_token = db.query(CustomerPasswordResetTokenDB).filter(
        CustomerPasswordResetTokenDB.token == payload.token
    ).first()

    if not reset_token:
        raise HTTPException(status_code=400, detail="This reset link is invalid.")
    if reset_token.used:
        raise HTTPException(status_code=400, detail="This reset link has already been used.")
    if reset_token.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="This reset link has expired. Request a new one.")

    customer = db.query(CustomerDB).filter(CustomerDB.id == reset_token.customer_id).first()
    if not customer:
        raise HTTPException(status_code=400, detail="This reset link is invalid.")

    customer.hashed_password = hash_password(payload.new_password)
    reset_token.used = True
    db.commit()

    return {"message": "Password reset successfully. You can now log in with your new password."}


def get_current_customer(
    authorization: str = Header(None), db: Session = Depends(get_db)
) -> CustomerDB:
    """FastAPI dependency mirroring get_current_user, but for customer tokens."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Your session expired. Log in again.")

    token = authorization.split(" ")[1]
    payload = decode_access_token(token)
    if not payload or "customer_id" not in payload:
        raise HTTPException(status_code=401, detail="Your session expired. Log in again.")

    customer = db.query(CustomerDB).filter(CustomerDB.id == payload["customer_id"]).first()
    if not customer:
        raise HTTPException(status_code=401, detail="Your session expired. Log in again.")

    return customer


@router.post("/verify-email")
@limiter.limit("10/minute")
def verify_customer_email(
    request: Request,
    payload: CustomerVerifyEmailRequest,
    current_customer: CustomerDB = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    """Mirrors routes/auth.py's /auth/verify-email — same OTP design (10-minute, single-use, 5 attempts)."""
    if current_customer.email_verified:
        return {"message": "This account's email is already verified."}
    try:
        email_verification_svc.check_code(db, current_customer.id, email_verification_svc.CUSTOMER_PURPOSE, payload.code)
    except email_verification_svc.VerificationCodeError as error:
        raise HTTPException(status_code=400, detail=str(error))
    current_customer.email_verified = True
    db.commit()
    return {"message": "Email verified."}


@router.post("/resend-verification")
@limiter.limit("3/minute")
def resend_customer_verification(request: Request, current_customer: CustomerDB = Depends(get_current_customer), db: Session = Depends(get_db)):
    if current_customer.email_verified:
        return {"message": "This account's email is already verified."}
    _issue_and_send_customer_verification_email(db, current_customer)
    return {"message": "A new verification code was sent to your email."}


@router.post("/refresh-token", response_model=CustomerRefreshTokenResponse)
@limiter.limit("30/minute")
def refresh_customer_access_token(request: Request, payload: CustomerRefreshTokenRequest, db: Session = Depends(get_db)):
    """Mirrors routes/auth.py's /auth/refresh — same rotation + theft-detection design, see models/refresh_token.py."""
    token_hash = hash_refresh_token(payload.refresh_token)
    stored = db.query(CustomerRefreshTokenDB).filter(CustomerRefreshTokenDB.token_hash == token_hash).first()

    if not stored:
        raise HTTPException(status_code=401, detail="Invalid refresh token. Log in again.")

    if stored.used or stored.revoked_at is not None:
        db.query(CustomerRefreshTokenDB).filter(
            CustomerRefreshTokenDB.customer_id == stored.customer_id,
            CustomerRefreshTokenDB.revoked_at.is_(None),
        ).update({"revoked_at": datetime.utcnow()})
        db.commit()
        raise HTTPException(status_code=401, detail="This session was revoked. Log in again.")

    if stored.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Your session has expired. Log in again.")

    customer = db.query(CustomerDB).filter(CustomerDB.id == stored.customer_id).first()
    if not customer:
        raise HTTPException(status_code=401, detail="Invalid refresh token. Log in again.")

    new_raw_token = generate_refresh_token()
    new_row = CustomerRefreshTokenDB(customer_id=customer.id, token_hash=hash_refresh_token(new_raw_token))
    db.add(new_row)
    db.flush()

    stored.used = True
    stored.replaced_by_id = new_row.id
    db.commit()

    new_access_token = create_access_token({"customer_id": customer.id})
    return {"access_token": new_access_token, "refresh_token": new_raw_token}


@router.post("/logout")
def customer_logout(payload: CustomerRefreshTokenRequest, db: Session = Depends(get_db)):
    """Mirrors routes/auth.py's /auth/logout — see that docstring for why this isn't access-token-gated."""
    token_hash = hash_refresh_token(payload.refresh_token)
    stored = db.query(CustomerRefreshTokenDB).filter(CustomerRefreshTokenDB.token_hash == token_hash).first()
    if stored and stored.revoked_at is None:
        stored.revoked_at = datetime.utcnow()
        db.commit()
    return {"message": "Logged out."}


@router.get("/me", response_model=CustomerOut)
def get_my_profile(current_customer: CustomerDB = Depends(get_current_customer)):
    return current_customer


@router.patch("/me", response_model=CustomerOut)
def update_my_profile(
    payload: CustomerProfileUpdate,
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name can't be empty.")
        current_customer.name = name

    if payload.email is not None:
        email = payload.email.strip()
        if not email:
            raise HTTPException(status_code=400, detail="Email can't be empty.")
        if email != current_customer.email:
            existing = db.query(CustomerDB).filter(CustomerDB.email == email).first()
            if existing:
                raise HTTPException(status_code=400, detail="Another account already uses that email.")
            current_customer.email = email

    db.commit()
    db.refresh(current_customer)
    return current_customer


@router.post("/me/change-password")
def change_my_password(
    payload: CustomerPasswordChange,
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    if not verify_password(payload.current_password, current_customer.hashed_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters.")

    current_customer.hashed_password = hash_password(payload.new_password)
    db.commit()
    return {"message": "Password changed."}


@router.post("/me/set-password")
def set_my_customer_password(
    payload: CustomerSetPassword,
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    """Customer-side equivalent of routes/auth.py's staff
    /me/set-password — see UserSetPassword's docstring for the
    reasoning in full; identical logic, customer side."""
    if current_customer.has_usable_password:
        raise HTTPException(
            status_code=400,
            detail="This account already has a password. Use \"Change password\" instead.",
        )
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

    current_customer.hashed_password = hash_password(payload.new_password)
    current_customer.has_usable_password = True
    db.commit()
    return {"message": "Password set. You can now log in with it, in addition to Google Sign-In."}


# ---------- Two-factor authentication (customers) ----------
# Mirrors the staff flow in routes/auth.py: authenticator app (scan a QR code
# with Google Authenticator / Microsoft Authenticator / Authy…) or a code
# emailed at login; setup is two-step (a real code must be confirmed before
# anything turns on); 8 one-time recovery codes; turning it off needs the
# password. Only for accounts that HAVE a password — a Google-only account is
# protected by Google's own security, and there'd be no password login for a
# second factor to guard.

def _require_password_account(customer: CustomerDB) -> None:
    if not customer.has_usable_password:
        raise HTTPException(
            status_code=400,
            detail="Two-factor authentication protects password logins. Add a password in Profile first "
                   "(Google sign-in is already protected by your Google account's own security).",
        )


@router.get("/2fa/status")
def customer_two_factor_status(current_customer: CustomerDB = Depends(get_current_customer)):
    return {"totp_enabled": current_customer.totp_enabled, "two_factor_method": current_customer.two_factor_method}


@router.post("/2fa/setup")
def customer_setup_two_factor(db: Session = Depends(get_db), current_customer: CustomerDB = Depends(get_current_customer)):
    """Start AUTHENTICATOR APP setup: returns the secret + otpauth:// URI to show as a QR code. Nothing is on yet."""
    _require_password_account(current_customer)
    if current_customer.totp_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is already on. Turn it off first to set up a new device.")
    secret = generate_secret()
    current_customer.totp_secret = secret
    db.commit()
    return {"secret": secret, "otpauth_uri": get_provisioning_uri(secret, current_customer.email)}


@router.post("/2fa/enable")
@limiter.limit("10/minute")
def customer_enable_two_factor(
    request: Request, payload: CustomerTwoFactorCode,
    db: Session = Depends(get_db), current_customer: CustomerDB = Depends(get_current_customer),
):
    """Confirms the authenticator method with one real code from the app, then turns 2FA on."""
    if not current_customer.totp_secret:
        raise HTTPException(status_code=400, detail="Start setup first, then enter a code from your authenticator app.")
    if current_customer.totp_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is already on.")
    if not verify_code(current_customer.totp_secret, payload.code):
        raise HTTPException(status_code=400, detail="Incorrect code. Check your authenticator app and try again.")
    current_customer.totp_enabled = True
    current_customer.two_factor_method = "totp"
    db.commit()
    codes = security_svc.generate_recovery_codes(db, current_customer.id)
    send_security_alert_email(current_customer.email, "Two-factor authentication was turned on for your account")
    return {"success": True, "message": "Two-factor authentication is now on.", "recovery_codes": codes}


@router.post("/2fa/setup-email")
@limiter.limit("5/minute")
def customer_setup_email_two_factor(request: Request, db: Session = Depends(get_db), current_customer: CustomerDB = Depends(get_current_customer)):
    """Start EMAIL-CODE setup: emails a confirmation code to the account's own address."""
    _require_password_account(current_customer)
    if current_customer.totp_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is already on. Turn it off first to switch methods.")
    return {"sent": True, "masked_email": _send_customer_email_2fa_code(db, current_customer, "enable")}


@router.post("/2fa/enable-email")
@limiter.limit("10/minute")
def customer_enable_email_two_factor(
    request: Request, payload: CustomerTwoFactorCode,
    db: Session = Depends(get_db), current_customer: CustomerDB = Depends(get_current_customer),
):
    if current_customer.totp_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is already on.")
    try:
        email_verification_svc.check_code(db, current_customer.id, CUSTOMER_2FA_ENABLE_PURPOSE, payload.code)
    except email_verification_svc.VerificationCodeError as error:
        raise HTTPException(status_code=400, detail=str(error))
    current_customer.totp_enabled = True
    current_customer.two_factor_method = "email"
    current_customer.totp_secret = None
    db.commit()
    codes = security_svc.generate_recovery_codes(db, current_customer.id)
    send_security_alert_email(current_customer.email, "Two-factor authentication was turned on for your account")
    return {"success": True, "message": "Two-factor authentication is now on.", "recovery_codes": codes}


@router.post("/2fa/disable")
@limiter.limit("5/minute")
def customer_disable_two_factor(
    request: Request, payload: CustomerTwoFactorDisable,
    db: Session = Depends(get_db), current_customer: CustomerDB = Depends(get_current_customer),
):
    """Needs the password again (an open session alone isn't enough to strip 2FA off)."""
    if not verify_password(payload.password, current_customer.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect password.")
    current_customer.totp_enabled = False
    current_customer.totp_secret = None
    current_customer.two_factor_method = "totp"
    db.commit()
    send_security_alert_email(current_customer.email, "Two-factor authentication was turned off on your account")
    return {"success": True, "message": "Two-factor authentication is now off."}


@router.post("/2fa/resend-code")
@limiter.limit("5/minute")
def customer_resend_two_factor_login_code(request: Request, payload: CustomerTwoFactorLoginVerify, db: Session = Depends(get_db)):
    """Sends a fresh login code to an email-2FA account (`code` is ignored; only the challenge token matters)."""
    customer = _customer_from_challenge(db, payload.challenge_token)
    if customer.two_factor_method != "email":
        raise HTTPException(status_code=400, detail="This account doesn't use email codes.")
    return {"sent": True, "masked_email": _send_customer_email_2fa_code(db, customer, "login")}


@router.post("/2fa/verify-login", response_model=CustomerTokenResponse)
@limiter.limit("10/minute")
def customer_verify_two_factor_login(request: Request, payload: CustomerTwoFactorLoginVerify, db: Session = Depends(get_db)):
    """Second login step: challenge token (from /customer/login) + a correct code (or a recovery code) -> real session."""
    customer = _customer_from_challenge(db, payload.challenge_token)
    code = payload.code.strip()

    valid = False
    if customer.two_factor_method == "email":
        try:
            email_verification_svc.check_code(db, customer.id, CUSTOMER_2FA_LOGIN_PURPOSE, code)
            valid = True
        except email_verification_svc.VerificationCodeError as error:
            if not security_svc.verify_and_consume_recovery_code(db, customer.id, code):
                raise HTTPException(status_code=401, detail=str(error))
            valid = True
    else:
        valid = bool(customer.totp_secret) and verify_code(customer.totp_secret, code)
        if not valid:
            valid = security_svc.verify_and_consume_recovery_code(db, customer.id, code)
    if not valid:
        raise HTTPException(status_code=401, detail="Incorrect code. Check your authenticator app, or use a recovery code.")

    token = create_access_token({"customer_id": customer.id})
    refresh_token = _issue_customer_refresh_token(db, customer.id)
    return {"access_token": token, "refresh_token": refresh_token, "customer": customer}
