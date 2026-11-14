"""
DeliveryChangeAuditDB — an auditable record of every DECISION the server made about a change to a
delivery that came from an agent/dispatcher device or the online status endpoint:

  accepted    the change was based on the current version and allowed by the transition policy
  reconciled  the change was based on an OLDER version, but is still valid against the current state
              (e.g. the dispatcher only edited a note meanwhile) and was applied
  rejected    the change was refused: stale and superseded (e.g. the delivery was cancelled after the
              agent last synced), a forbidden status transition, wrong owner/organization, ...

This complements DeliveryHistoryDB (which only lists status changes that were applied). Here the
REJECTED and RECONCILED outcomes are kept too, so "why does the app show delivered when I marked it
failed?" always has an answer.

Two different clocks are stored on purpose:
  occurred_at / client_occurred_at  when the event happened on the device (corrected / raw)
  recorded_at                       when the server decided about it (always the real server time)
and the monotonic order of applied changes is given by version_before -> version_after, never by
comparing timestamps.
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Integer, Float
from pydantic import BaseModel
from typing import Optional

from app.db.session import Base


class DeliveryChangeAuditDB(Base):
    __tablename__ = "delivery_change_audit"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    org_id = Column(String, index=True, nullable=False)
    delivery_id = Column(String, index=True, nullable=False)
    order_id = Column(String, nullable=True)

    outcome = Column(String, index=True, nullable=False)   # "accepted" | "reconciled" | "rejected"
    source = Column(String, nullable=False)                # "offline_sync" | "online"
    code = Column(String, nullable=True)                   # why (rejections/reconciliations): stale_version, transition_not_allowed, ...
    message = Column(String, nullable=True)                # human-readable explanation

    actor_user_id = Column(String, nullable=True)
    actor_display_name = Column(String, nullable=True)
    actor_role = Column(String, nullable=True)

    base_version = Column(Integer, nullable=True)          # the version the client had read
    version_before = Column(Integer, nullable=True)        # the server's version when the change arrived
    version_after = Column(Integer, nullable=True)         # the server's version after (== before if rejected)

    status_before = Column(String, nullable=True)          # the server's status when the change arrived
    status_after = Column(String, nullable=True)           # accepted/reconciled: new status; rejected: the status that was ATTEMPTED

    occurred_at = Column(DateTime, nullable=True)          # when it happened (device time, corrected if the device clock was off)
    client_occurred_at = Column(DateTime, nullable=True)   # the raw time the device reported
    clock_offset_seconds = Column(Float, nullable=True)    # server time minus device time at upload (None if within tolerance)
    recorded_at = Column(DateTime, nullable=False, default=datetime.utcnow)  # server's real time


class DeliveryChangeAuditOut(BaseModel):
    id: str
    delivery_id: str
    order_id: Optional[str] = None
    outcome: str
    source: str
    code: Optional[str] = None
    message: Optional[str] = None
    actor_display_name: Optional[str] = None
    actor_role: Optional[str] = None
    base_version: Optional[int] = None
    version_before: Optional[int] = None
    version_after: Optional[int] = None
    status_before: Optional[str] = None
    status_after: Optional[str] = None
    occurred_at: Optional[datetime] = None
    client_occurred_at: Optional[datetime] = None
    clock_offset_seconds: Optional[float] = None
    recorded_at: datetime

    class Config:
        from_attributes = True
