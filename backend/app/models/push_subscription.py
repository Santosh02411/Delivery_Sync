"""
PushSubscriptionDB: stores browser Web Push subscriptions so a customer
can get a real OS-level notification (even with the tab closed) when
their delivery's status changes — no third-party account needed, unlike
SMS/WhatsApp. Web Push is a free, open, standardized browser API backed
by VAPID keys this project generates itself (see services/push.py).

One customer can have multiple subscriptions (different browsers/
devices) -- each is a separate row, keyed by its unique endpoint URL.
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime
from pydantic import BaseModel

from app.db.session import Base


class PushSubscriptionDB(Base):
    __tablename__ = "push_subscriptions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id = Column(String, index=True, nullable=False)
    endpoint = Column(String, nullable=False, unique=True)
    p256dh = Column(String, nullable=False)
    auth = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)


class PushSubscriptionCreate(BaseModel):
    endpoint: str
    keys: dict  # {"p256dh": "...", "auth": "..."} -- the raw shape the browser's PushManager gives us
