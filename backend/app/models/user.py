"""
User model — supports two roles: "agent" and "dispatcher".

This is intentionally simple (username + password only, no email
verification, no password reset flow) since the goal is a real, working
role-based auth system for this project's scope — not a full production
identity system.
"""

import enum
import uuid

from sqlalchemy import Column, String, Enum as SqlEnum
from pydantic import BaseModel

from app.db.session import Base


class UserRole(str, enum.Enum):
    agent = "agent"
    dispatcher = "dispatcher"


class UserDB(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(SqlEnum(UserRole), nullable=False)
    display_name = Column(String, nullable=False)


# ---------- Pydantic Schemas ----------

class UserSignup(BaseModel):
    username: str
    password: str
    role: UserRole
    display_name: str


class UserLogin(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: str
    username: str
    role: UserRole
    display_name: str

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
