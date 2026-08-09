"""
Public delivery tracking — lets a customer check status using just the
delivery's ID as a "tracking code," with NO login required. This is
deliberately a separate, minimal response shape from the internal
DeliveryRecordOut: it never exposes agent identity, organization info,
or internal notes — just what a customer actually needs to see.

Using the delivery's own ID (a UUID) as the tracking code, rather than
generating a separate random code, is intentional: UUIDs are already
unguessable (practically impossible to enumerate), so a second code
would add complexity without adding real security. Rate-limited below to
guard against anyone still trying to brute-force/scrape it anyway.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from app.db.session import get_db
from app.models.delivery import DeliveryRecordDB
from app.models.delivery_history import DeliveryHistoryDB
from app.models.feedback import DeliveryFeedbackDB, FeedbackSubmit, FeedbackOut
from app.services.rate_limiter import limiter

router = APIRouter(prefix="/track", tags=["public-tracking"])


class PublicHistoryEntry(BaseModel):
    old_status: Optional[str] = None
    new_status: str
    changed_at: datetime


class PublicTrackingOut(BaseModel):
    order_id: str
    status: str
    zone: Optional[str] = None
    expected_by: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    proof_of_delivery: Optional[str] = None
    history: List[PublicHistoryEntry] = []
    feedback: Optional[FeedbackOut] = None


@router.get("/{delivery_id}", response_model=PublicTrackingOut)
@limiter.limit("30/minute")
def track_delivery(request: Request, delivery_id: str, db: Session = Depends(get_db)):
    delivery = db.query(DeliveryRecordDB).filter(DeliveryRecordDB.id == delivery_id).first()
    if not delivery:
        raise HTTPException(status_code=404, detail="No delivery found for this tracking link.")

    history_rows = (
        db.query(DeliveryHistoryDB)
        .filter(DeliveryHistoryDB.delivery_id == delivery_id)
        .order_by(DeliveryHistoryDB.changed_at.asc())
        .all()
    )

    existing_feedback = db.query(DeliveryFeedbackDB).filter(
        DeliveryFeedbackDB.delivery_id == delivery_id
    ).first()

    return PublicTrackingOut(
        order_id=delivery.order_id,
        status=delivery.status.value,
        zone=delivery.zone,
        expected_by=delivery.expected_by,
        created_at=delivery.created_at,
        updated_at=delivery.updated_at,
        proof_of_delivery=delivery.proof_of_delivery,
        history=[
            PublicHistoryEntry(
                old_status=h.old_status,
                new_status=h.new_status,
                changed_at=h.changed_at,
            )
            for h in history_rows
        ],
        feedback=existing_feedback,
    )


@router.post("/{delivery_id}/feedback", response_model=FeedbackOut)
@limiter.limit("5/minute")
def submit_feedback(
    request: Request,
    delivery_id: str,
    payload: FeedbackSubmit,
    db: Session = Depends(get_db),
):
    """
    Public, no-login feedback submission — only allowed once a delivery
    is actually marked "Delivered" (rating a delivery that hasn't
    happened yet doesn't make sense), and only once per delivery (a
    second submission attempt is rejected, not silently overwritten —
    the customer already has one recorded rating).
    """
    delivery = db.query(DeliveryRecordDB).filter(DeliveryRecordDB.id == delivery_id).first()
    if not delivery:
        raise HTTPException(status_code=404, detail="No delivery found for this tracking link.")

    if delivery.status.value != "delivered":
        raise HTTPException(status_code=400, detail="Feedback can only be left after delivery is complete.")

    existing = db.query(DeliveryFeedbackDB).filter(DeliveryFeedbackDB.delivery_id == delivery_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Feedback has already been submitted for this delivery.")

    feedback = DeliveryFeedbackDB(
        delivery_id=delivery_id,
        rating=payload.rating,
        comment=payload.comment,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return feedback
