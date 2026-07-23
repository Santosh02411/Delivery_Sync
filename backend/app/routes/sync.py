"""
The /sync endpoint — this is what the client calls when connectivity
returns, sending a batch of records that were saved offline in IndexedDB.

For each record, conflict resolution is applied (see services/conflict_resolver.py).
The response tells the client the FINAL state of each record, so the client
can update its local IndexedDB copy if the server's version won a conflict.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List

from app.db.session import get_db
from app.models.delivery import DeliveryRecordOut, DeliveryStatus
from app.services.conflict_resolver import resolve_and_apply
from datetime import datetime

router = APIRouter(tags=["sync"])


class SyncRecordIn(BaseModel):
    """Shape of each record sent in a sync batch from the client."""
    id: str
    agent_id: str
    order_id: str
    status: DeliveryStatus
    notes: str | None = None
    location_note: str | None = None
    created_at: datetime
    updated_at: datetime


class SyncRequest(BaseModel):
    records: List[SyncRecordIn]


class SyncResponse(BaseModel):
    resolved_records: List[DeliveryRecordOut]


@router.post("/sync", response_model=SyncResponse)
def sync_records(payload: SyncRequest, db: Session = Depends(get_db)):
    """
    Accepts a batch of offline records and resolves each one against the
    server's current state. Returns the final resolved version of every
    record so the client can reconcile its local IndexedDB copy.
    """
    resolved = []
    for record in payload.records:
        final_record = resolve_and_apply(record.model_dump(), db)
        resolved.append(final_record)

    return {"resolved_records": resolved}
