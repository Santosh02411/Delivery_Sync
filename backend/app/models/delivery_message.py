"""
Per-delivery messages — a simple chat/notes thread between the assigned
agent and their organization's dispatchers/admins, scoped to one specific
delivery. Distinct from the `notes` field on a delivery (a single
current-state note) and from the status history log (an automatic audit
trail) — this is an actual back-and-forth conversation thread.
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime
from pydantic import BaseModel

from app.db.session import Base


class DeliveryMessageDB(Base):
    __tablename__ = "delivery_messages"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    delivery_id = Column(String, index=True, nullable=False)
    sender_id = Column(String, nullable=False)
    sender_display_name = Column(String, nullable=False)  # denormalized, same reasoning as delivery_history
    sender_role = Column(String, nullable=False)  # "agent", "dispatcher", or "admin" at time of sending
    message = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class MessageCreate(BaseModel):
    message: str


class MessageOut(BaseModel):
    id: str
    delivery_id: str
    sender_id: str
    sender_display_name: str
    sender_role: str
    message: str
    created_at: datetime

    class Config:
        from_attributes = True
