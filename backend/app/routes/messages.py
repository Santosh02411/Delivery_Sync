"""
Per-delivery messaging routes. Access rules:
- An agent can only read/send messages on deliveries ASSIGNED TO THEM.
- A dispatcher/admin can read/send messages on any delivery in their own
  organization (they're coordinating across all their agents' work).
- Everything is still org-scoped underneath — an agent or dispatcher from
  a different organization can never reach another org's delivery thread,
  same isolation guarantee as every other endpoint in this app.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.models.delivery import DeliveryRecordDB
from app.models.delivery_message import DeliveryMessageDB, MessageCreate, MessageOut
from app.models.user import UserDB, UserRole
from app.routes.auth import get_current_user

router = APIRouter(prefix="/deliveries", tags=["messages"])


def _get_authorized_delivery(delivery_id: str, current_user: UserDB, db: Session) -> DeliveryRecordDB:
    """Shared access check for both the GET and POST message endpoints."""
    delivery = db.query(DeliveryRecordDB).filter(DeliveryRecordDB.id == delivery_id).first()
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found.")

    if delivery.org_id != current_user.org_id:
        raise HTTPException(status_code=404, detail="Delivery not found.")

    if current_user.role == UserRole.agent and delivery.agent_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only message about your own deliveries.")

    return delivery


@router.get("/{delivery_id}/messages", response_model=List[MessageOut])
def list_messages(
    delivery_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    _get_authorized_delivery(delivery_id, current_user, db)
    return (
        db.query(DeliveryMessageDB)
        .filter(DeliveryMessageDB.delivery_id == delivery_id)
        .order_by(DeliveryMessageDB.created_at.asc())
        .all()
    )


@router.post("/{delivery_id}/messages", response_model=MessageOut)
def send_message(
    delivery_id: str,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    _get_authorized_delivery(delivery_id, current_user, db)

    if not payload.message or not payload.message.strip():
        raise HTTPException(status_code=400, detail="Message can't be empty.")

    message = DeliveryMessageDB(
        delivery_id=delivery_id,
        sender_id=current_user.id,
        sender_display_name=current_user.display_name,
        sender_role=current_user.role.value,
        message=payload.message.strip(),
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message
