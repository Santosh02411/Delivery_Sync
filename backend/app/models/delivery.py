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
    picked_up = "picked_up"
    out_for_delivery = "out_for_delivery"
    delivered = "delivered"
    failed_attempt = "failed_attempt"


# ---------- Database Table ----------

class DeliveryRecordDB(Base):
    __tablename__ = "deliveries"

    id = Column(String, primary_key=True, index=True)
    agent_id = Column(String, index=True, nullable=False)
    order_id = Column(String, index=True, nullable=False)
    status = Column(SqlEnum(DeliveryStatus), nullable=False)
    notes = Column(String, nullable=True)
    location_note = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


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


class DeliveryRecordUpdate(BaseModel):
    """Shape expected when updating an existing delivery record's status."""
    status: DeliveryStatus
    notes: Optional[str] = None
    location_note: Optional[str] = None
    updated_at: datetime


class DeliveryRecordOut(BaseModel):
    """Shape returned by the API to clients."""
    id: str
    agent_id: str
    order_id: str
    status: DeliveryStatus
    notes: Optional[str] = None
    location_note: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True  # allows conversion from SQLAlchemy objects