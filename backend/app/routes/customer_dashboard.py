"""
Customer dashboard — the REAL, logged-in customer experience: every
delivery linked to this customer's account (across ANY organization
using this platform, not just one), plus their in-app notification
inbox. This is what a customer actually uses day-to-day, as opposed to
the one-off public tracking link (still available for guests without an
account, or for sharing a single order with someone else).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db.session import get_db
from app.models.delivery import DeliveryRecordDB, DeliveryRecordOut, ClaimOrderRequest
from app.models.delivery_history import DeliveryHistoryDB, DeliveryHistoryOut
from app.models.customer import CustomerDB
from app.models.customer_notification import CustomerNotificationDB, CustomerNotificationOut
from app.models.feedback import DeliveryFeedbackDB, FeedbackOut
from app.routes.customer_auth import get_current_customer

router = APIRouter(prefix="/customer", tags=["customer-dashboard"])


def _normalize_phone(raw: str) -> str:
    """Digits only, so '+91 98765-43210' and '9876543210' compare equal."""
    return "".join(ch for ch in raw if ch.isdigit())


@router.post("/deliveries/claim", response_model=DeliveryRecordOut)
def claim_order(
    payload: ClaimOrderRequest,
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    """
    Manually link an order to the logged-in customer's account, for the
    case where it wasn't auto-linked (the dispatcher entered a different
    email than the one this customer signed up with — a common real-world
    mismatch: guest checkout, a typo, a work vs personal email, etc.).

    Verified with order_id + phone together, since order_id alone (a
    dispatcher-chosen reference number) isn't secret or guaranteed unique
    across organizations, so it can't prove ownership by itself.
    """
    normalized_input_phone = _normalize_phone(payload.phone)
    if not normalized_input_phone:
        raise HTTPException(status_code=400, detail="Enter the phone number on file for this order.")

    candidates = db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.order_id == payload.order_id,
        DeliveryRecordDB.customer_phone.isnot(None),
    ).all()

    match = next(
        (d for d in candidates if _normalize_phone(d.customer_phone) == normalized_input_phone),
        None,
    )

    if not match:
        raise HTTPException(
            status_code=404,
            detail="No order found with that Order ID and phone number. Double-check both and try again.",
        )

    if match.customer_id and match.customer_id != current_customer.id:
        raise HTTPException(
            status_code=409,
            detail="This order is already linked to a different account.",
        )

    match.customer_id = current_customer.id
    db.commit()
    db.refresh(match)
    return match


@router.get("/deliveries", response_model=List[DeliveryRecordOut])
def list_my_deliveries(
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    """Every delivery linked to this customer's account, across any organization."""
    return (
        db.query(DeliveryRecordDB)
        .filter(DeliveryRecordDB.customer_id == current_customer.id)
        .order_by(DeliveryRecordDB.updated_at.desc())
        .all()
    )


@router.get("/deliveries/{delivery_id}/history", response_model=List[DeliveryHistoryOut])
def get_my_delivery_history(
    delivery_id: str,
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    """Full status timeline for one of the customer's own deliveries."""
    delivery = db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.id == delivery_id,
        DeliveryRecordDB.customer_id == current_customer.id,
    ).first()
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found.")

    return (
        db.query(DeliveryHistoryDB)
        .filter(DeliveryHistoryDB.delivery_id == delivery_id)
        .order_by(DeliveryHistoryDB.changed_at.asc())
        .all()
    )


@router.get("/deliveries/{delivery_id}/feedback", response_model=Optional[FeedbackOut])
def get_my_delivery_feedback(
    delivery_id: str,
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    delivery = db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.id == delivery_id,
        DeliveryRecordDB.customer_id == current_customer.id,
    ).first()
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found.")

    return db.query(DeliveryFeedbackDB).filter(DeliveryFeedbackDB.delivery_id == delivery_id).first()


@router.get("/notifications", response_model=List[CustomerNotificationOut])
def list_my_notifications(
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    return (
        db.query(CustomerNotificationDB)
        .filter(CustomerNotificationDB.customer_id == current_customer.id)
        .order_by(CustomerNotificationDB.created_at.desc())
        .all()
    )


@router.patch("/notifications/{notification_id}/read", response_model=CustomerNotificationOut)
def mark_notification_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    notification = db.query(CustomerNotificationDB).filter(
        CustomerNotificationDB.id == notification_id,
        CustomerNotificationDB.customer_id == current_customer.id,
    ).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found.")

    notification.is_read = True
    db.commit()
    db.refresh(notification)
    return notification


@router.post("/notifications/mark-all-read")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    db.query(CustomerNotificationDB).filter(
        CustomerNotificationDB.customer_id == current_customer.id,
        CustomerNotificationDB.is_read == False,  # noqa: E712
    ).update({"is_read": True})
    db.commit()
    return {"message": "All notifications marked as read."}
