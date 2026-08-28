"""
DeliveryRecord model.

This file defines TWO things, which is a common FastAPI pattern:
1. `DeliveryRecordDB`  -> the actual database table (SQLAlchemy)
2. Pydantic schemas    -> what data looks like coming in/out of the API

Keeping these separate means the API can validate/shape data independently
of how it's stored in the database.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Enum as SqlEnum
from pydantic import BaseModel, Field
from typing import Optional

from app.db.session import Base


class DeliveryStatus(str, enum.Enum):
    pending = "pending"  # placed via checkout, not yet assigned to an agent
    picked_up = "picked_up"
    out_for_delivery = "out_for_delivery"
    delivered = "delivered"
    failed_attempt = "failed_attempt"
    cancelled = "cancelled"


# ---------- Database Table ----------

class DeliveryRecordDB(Base):
    __tablename__ = "deliveries"

    id = Column(String, primary_key=True, index=True)
    agent_id = Column(String, index=True, nullable=True)
    order_id = Column(String, index=True, nullable=False)
    status = Column(SqlEnum(DeliveryStatus), nullable=False)
    notes = Column(String, nullable=True)
    location_note = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    # Added for route batching: a free-text zone/area name (e.g. "Sector 5",
    # "Koramangala") lets deliveries be grouped without needing a paid
    # geocoding API. Latitude/longitude are optional — if a dispatcher
    # knows them, the agent's route can additionally be ordered via a
    # nearest-neighbor heuristic; deliveries without coordinates still work
    # fine, just grouped by zone rather than precisely ordered.
    zone = Column(String, nullable=True)
    latitude = Column(String, nullable=True)
    longitude = Column(String, nullable=True)

    # Added for delivery time estimates: an optional deadline the
    # dispatcher sets when assigning. Nullable — not every delivery needs
    # a hard deadline.
    expected_by = Column(DateTime, nullable=True)

    # Delivery time-slot the CUSTOMER picked at checkout (see
    # routes/slots.py, routes/checkout.py) — distinct from expected_by
    # above, which is a dispatcher-set deadline on an already-placed
    # delivery. Both nullable: a dispatcher-created manual delivery has
    # neither, and a customer checkout without picking a slot (ASAP
    # delivery) has neither either.
    slot_start = Column(DateTime, nullable=True)
    slot_end = Column(DateTime, nullable=True)

    # Multi-tenant scoping: every delivery belongs to exactly one
    # organization (inherited from the dispatcher who created it). All
    # queries elsewhere filter by this so two organizations never see
    # each other's deliveries.
    org_id = Column(String, index=True, nullable=False)

    # Proof of delivery: a base64 data URL (signature drawn on a canvas,
    # or a photo taken/uploaded), captured when the agent marks a
    # delivery "Delivered". Stored directly in the database as text —
    # there's no budget for cloud file storage (S3, etc.) in this
    # project, so this is a disclosed, demo-appropriate simplification.
    # A production system would upload to real object storage and store
    # a URL here instead of the raw image data itself.
    proof_of_delivery = Column(String, nullable=True)

    # Customer contact info, used to send status-change notifications
    # (see services/notifications.py). Both optional — a delivery can
    # have neither, either, or both; notifications simply aren't sent
    # for whichever channel has no contact info.
    customer_email = Column(String, nullable=True)
    customer_phone = Column(String, nullable=True)

    # If a real customer account (CustomerDB) exists matching
    # customer_email, this gets set — that's what makes the delivery show
    # up in that customer's dashboard, not just via a one-off tracking
    # link. Nullable: plenty of deliveries won't have a matching customer
    # account, and that's fine — they're still trackable via the public
    # link, just not tied to a logged-in dashboard.
    customer_id = Column(String, nullable=True, index=True)

    # "delivery" (default — a normal forward delivery to the customer)
    # or "return_pickup" (an agent collecting an item BACK from the
    # customer, created when a return/exchange request is approved —
    # see models/return_request.py). A return_pickup flows through the
    # exact same pending -> picked_up -> delivered status lifecycle as
    # a normal delivery; "delivered" for one of these means "brought
    # back to the store", which is what triggers the return/exchange to
    # complete (see routes/deliveries.py's update_delivery).
    delivery_type = Column(String, nullable=False, default="delivery")


# ---------- Pydantic Schemas (API request/response shapes) ----------

class DeliveryRecordCreate(BaseModel):
    """Shape expected when creating a new delivery record."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str
    order_id: str
    status: DeliveryStatus
    notes: Optional[str] = None
    location_note: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    zone: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    expected_by: Optional[datetime] = None
    slot_start: Optional[datetime] = None
    slot_end: Optional[datetime] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None


class ClaimOrderRequest(BaseModel):
    """
    Shape for a customer manually linking an order to their account when
    it wasn't auto-linked at creation time (e.g. the dispatcher typed a
    different email than the one the customer signed up with). Verified
    with the order's phone number rather than trusting order_id alone —
    order_id is a dispatcher-chosen business reference, not guaranteed
    globally unique or secret, so it can't be the only proof of ownership.
    """
    order_id: str
    phone: str


class DeliveryRecordUpdate(BaseModel):
    """Shape expected when updating an existing delivery record's status."""
    status: DeliveryStatus
    notes: Optional[str] = None
    location_note: Optional[str] = None
    updated_at: datetime
    proof_of_delivery: Optional[str] = None


class DeliveryRecordOut(BaseModel):
    """Shape returned by the API to clients."""
    id: str
    agent_id: Optional[str] = None
    order_id: str
    status: DeliveryStatus
    notes: Optional[str] = None
    location_note: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    zone: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    expected_by: Optional[datetime] = None
    slot_start: Optional[datetime] = None
    slot_end: Optional[datetime] = None
    org_id: str
    proof_of_delivery: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_id: Optional[str] = None
    delivery_type: str = "delivery"

    class Config:
        from_attributes = True  # allows conversion from SQLAlchemy objects
