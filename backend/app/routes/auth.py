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
from app.models.user import UserDB, UserSignup, UserLogin, UserOut, TokenResponse
from app.models.organization import OrganizationDB
from app.models.password_reset import PasswordResetTokenDB, ForgotPasswordRequest, ResetPasswordRequest
from app.services.auth import hash_password, verify_password, create_access_token, decode_access_token
from app.services.rate_limiter import limiter
from app.services.email import send_password_reset_email
from datetime import datetime
import os

router = APIRouter(prefix="/auth", tags=["auth"])

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3001")


def generate_invite_code() -> str:
    """8-character, easy-to-read invite code (uppercase letters + digits)."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


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


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.username == payload.username).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect username or password.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been deactivated. Contact your admin.")

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
