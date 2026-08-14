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
from sqlalchemy import Column, String, DateTime, Boolean, Float, Integer
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
    # storefront (GET /stores). Defaults ON so a newly created org's
    # products are immediately visible to customers without an extra
    # manual step — admins can still turn it off from the Products tab
    # for orgs that are purely internal courier/logistics operations.
    is_public_store = Column(Boolean, nullable=False, default=True)

    # Checkout pricing, admin-configurable per org — see routes/products.py's
    # store_router for the PATCH endpoint. Defaults are non-zero (a
    # typical flat delivery fee + India's most common GST slab) so the
    # fee/tax line actually shows up out of the box rather than looking
    # unimplemented; an admin can zero either out for their own store.
    delivery_fee = Column(Float, nullable=False, default=40.0)
    tax_rate_percent = Column(Float, nullable=False, default=5.0)  # GST %, applied to the post-discount subtotal

    # Delivery time-slot scheduling, admin-configurable per org — see
    # routes/slots.py. Defines the daily operating window (24h clock)
    # and how it's chopped into bookable slots. Defaults: a 9am-9pm day
    # cut into 2-hour windows, max 10 orders per slot — reasonable
    # out-of-the-box behavior for a store that hasn't touched this
    # setting yet, same reasoning as the pricing defaults above.
    slot_duration_minutes = Column(Integer, nullable=False, default=120)
    slot_window_start_hour = Column(Integer, nullable=False, default=9)   # 0-23
    slot_window_end_hour = Column(Integer, nullable=False, default=21)    # 0-23, exclusive of the last slot's end
    max_orders_per_slot = Column(Integer, nullable=False, default=10)


class OrganizationOut(BaseModel):
    id: str
    name: str
    invite_code: str
    is_public_store: bool = False
    delivery_fee: float = 0.0
    tax_rate_percent: float = 0.0
    slot_duration_minutes: int = 120
    slot_window_start_hour: int = 9
    slot_window_end_hour: int = 21
    max_orders_per_slot: int = 10

    class Config:
        from_attributes = True


class StoreVisibilityUpdate(BaseModel):
    is_public_store: bool


class StorePricingUpdate(BaseModel):
    delivery_fee: float
    tax_rate_percent: float


class StoreSlotSettingsUpdate(BaseModel):
    slot_duration_minutes: int
    slot_window_start_hour: int
    slot_window_end_hour: int
    max_orders_per_slot: int


class PublicOrganizationOut(BaseModel):
    """
    Public storefront listing shape — deliberately excludes invite_code.
    That code lets someone join the organization as staff (agent/
    dispatcher/admin), so it must never appear anywhere a customer or
    anonymous visitor can see it, unlike OrganizationOut above (used
    only in authenticated staff-facing responses). delivery_fee/
    tax_rate_percent/slot_* ARE included here — a customer needs them to
    preview their total and pick a delivery window before checking out.
    """
    id: str
    name: str
    is_public_store: bool = True
    delivery_fee: float = 0.0
    tax_rate_percent: float = 0.0
    slot_duration_minutes: int = 120
    slot_window_start_hour: int = 9
    slot_window_end_hour: int = 21

    class Config:
        from_attributes = True


class DeliverySlotOut(BaseModel):
    start: datetime
    end: datetime
    available: bool
    remaining: int
    capacity: int
