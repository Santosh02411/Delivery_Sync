"""
Customer accounts — deliberately a SEPARATE identity system from the
staff UserDB (agents/dispatchers/admins). A customer isn't a member of
any single organization; they may have deliveries from many different
companies using this platform, so they don't fit the org-scoped staff
model at all. This is its own simple email+password account.
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Boolean
from pydantic import BaseModel
from typing import Optional

from app.db.session import Base


class CustomerDB(Base):
    __tablename__ = "customers"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # See UserDB.email_verified's comment (models/user.py) — same idea,
    # same "informational, not a login gate" behavior, kept as a
    # separate column here rather than shared logic since CustomerDB and
    # UserDB are entirely separate identity systems.
    email_verified = Column(Boolean, nullable=False, default=False)


class CustomerSignup(BaseModel):
    email: str
    password: str
    name: str
    captcha_token: Optional[str] = None


class CustomerLogin(BaseModel):
    email: str
    password: str


class CustomerOut(BaseModel):
    id: str
    email: str
    name: str
    email_verified: bool = False

    class Config:
        from_attributes = True


class CustomerProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None


class CustomerPasswordChange(BaseModel):
    current_password: str
    new_password: str


class CustomerUpdate(BaseModel):
    name: str


class CustomerPasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class CustomerTokenResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    customer: CustomerOut
