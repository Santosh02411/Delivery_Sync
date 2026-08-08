"""
Organization model — the foundation of multi-tenant support.

Every user and every delivery belongs to exactly one organization. All
queries elsewhere in the app filter by the current user's org_id, so two
different companies using the same deployment never see each other's
data.

Design: the FIRST user to sign up for a new organization becomes its
"admin" automatically (regardless of what role they picked at signup) —
someone has to be able to manage the org's users, and requiring a
separate manual promotion step for the very first user would be a chicken-
and-egg problem. Every subsequent signup must provide that organization's
invite_code to join it, choosing agent/dispatcher/admin themselves.
"""

import uuid
from sqlalchemy import Column, String, DateTime
from pydantic import BaseModel
from datetime import datetime

from app.db.session import Base


class OrganizationDB(Base):
    __tablename__ = "organizations"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    invite_code = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class OrganizationOut(BaseModel):
    id: str
    name: str
    invite_code: str

    class Config:
        from_attributes = True
