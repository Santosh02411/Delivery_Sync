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
from sqlalchemy import Column, String, DateTime, Boolean
from pydantic import BaseModel
from datetime import datetime

from app.db.session import Base


class OrganizationDB(Base):
    __tablename__ = "organizations"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    invite_code = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Whether this org's product catalog is visible on the public
    # storefront (GET /stores). Off by default — an org has to
    # deliberately opt in to selling to walk-in customers, since not
    # every organization using this platform is a retail storefront
    # (some are purely internal courier/logistics operations).
    is_public_store = Column(Boolean, nullable=False, default=False)


class OrganizationOut(BaseModel):
    id: str
    name: str
    invite_code: str
    is_public_store: bool = False

    class Config:
        from_attributes = True


class StoreVisibilityUpdate(BaseModel):
    is_public_store: bool


class PublicOrganizationOut(BaseModel):
    """
    Public storefront listing shape — deliberately excludes invite_code.
    That code lets someone join the organization as staff (agent/
    dispatcher/admin), so it must never appear anywhere a customer or
    anonymous visitor can see it, unlike OrganizationOut above (used
    only in authenticated staff-facing responses).
    """
    id: str
    name: str
    is_public_store: bool = True

    class Config:
        from_attributes = True
