"""
User model — supports three roles: "agent", "dispatcher", and "admin".

Every user belongs to exactly one organization (org_id) — see
models/organization.py for how that's established at signup.

Design decision on usernames: kept GLOBALLY unique (not just unique
within an org), so login only ever needs a username + password with no
extra org context — a user picks their org at signup time (create one, or
join via invite code), and every login afterward is unambiguous. The
trade-off: two different organizations can't both have a user named
"admin", for example. For this project's scope, that's an acceptable
simplification worth being able to explain, rather than adding org-scoped
login (which would need the login form to ask "which organization?" too).
"""

import enum
import uuid

from sqlalchemy import Column, String, Enum as SqlEnum, Boolean, Float
from pydantic import BaseModel
from typing import Optional

from app.db.session import Base


class UserRole(str, enum.Enum):
    agent = "agent"
    dispatcher = "dispatcher"
    admin = "admin"


class UserDB(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(SqlEnum(UserRole), nullable=False)
    display_name = Column(String, nullable=False)
    org_id = Column(String, index=True, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)

    # Two-factor auth (TOTP). totp_secret is written as soon as the user
    # starts setup (see routes/auth.py's /2fa/setup) but totp_enabled
    # stays False until they prove they scanned it correctly by
    # submitting one valid code (/2fa/enable) — this prevents a user
    # locking themselves out by enabling 2FA against a secret their
    # authenticator app never actually received. A stale, never-confirmed
    # secret from an abandoned setup attempt is harmless: login only
    # checks totp_secret when totp_enabled is True.
    totp_secret = Column(String, nullable=True)
    totp_enabled = Column(Boolean, nullable=False, default=False)
    # Which second factor is active when totp_enabled is True: "totp" (an
    # authenticator app, using totp_secret above) or "email" (a one-time
    # code sent to `email` at login time — see services/email_otp.py).
    # Meaningless while totp_enabled is False.
    two_factor_method = Column(String, nullable=False, default="totp")

    # An agent's real-world coverage area — set via "Detect my area" on
    # their profile (browser GPS -> services/geocoding.py reverse
    # geocode), NOT hand-typed, so it reflects where the agent actually
    # is rather than a guess. Used to prioritize zone-matched agents when
    # a dispatcher assigns or auto-assigns a delivery that has a `zone`
    # set (see routes/deliveries.py's _rank_agents_for_delivery).
    # Meaningless for non-agent roles.
    area_name = Column(String, nullable=True)
    area_latitude = Column(Float, nullable=True)
    area_longitude = Column(Float, nullable=True)


# ---------- Pydantic Schemas ----------

class UserSignup(BaseModel):
    username: str
    email: str
    password: str
    role: UserRole
    display_name: str
    # Provide exactly one of these two: org_name to CREATE a new
    # organization (this user becomes its admin automatically), or
    # invite_code to JOIN an existing one with the role chosen above.
    org_name: Optional[str] = None
    invite_code: Optional[str] = None


class UserLogin(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: str
    username: str
    email: str
    role: UserRole
    display_name: str
    org_id: str
    is_active: bool
    totp_enabled: bool = False
    two_factor_method: str = "totp"
    area_name: Optional[str] = None

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    org_invite_code: Optional[str] = None  # only present when a NEW org was just created


class LoginResult(BaseModel):
    """
    Response shape for POST /auth/login. Covers both outcomes with one
    model rather than two, since FastAPI needs a single response_model:
    a normal login (access_token + user populated, requires_2fa False),
    or a 2FA challenge (only requires_2fa + challenge_token populated —
    no access_token yet, since the password alone isn't enough to prove
    identity for an account with 2FA turned on).
    """
    access_token: Optional[str] = None
    token_type: str = "bearer"
    user: Optional[UserOut] = None
    org_invite_code: Optional[str] = None
    requires_2fa: bool = False
    challenge_token: Optional[str] = None
    two_factor_method: Optional[str] = None  # "totp" or "email", only set when requires_2fa is True
    masked_email: Optional[str] = None  # e.g. "m***y@example.com" — only set when two_factor_method is "email"


class TwoFactorSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class TwoFactorCodeRequest(BaseModel):
    code: str


class TwoFactorLoginVerify(BaseModel):
    challenge_token: str
    code: str


class TwoFactorDisableRequest(BaseModel):
    password: str


class TwoFactorStatusOut(BaseModel):
    totp_enabled: bool
    two_factor_method: str = "totp"


class TwoFactorEmailCodeRequest(BaseModel):
    code: str


class AreaDetectRequest(BaseModel):
    latitude: float
    longitude: float


class AreaOut(BaseModel):
    area_name: Optional[str] = None
    area_latitude: Optional[float] = None
    area_longitude: Optional[float] = None
