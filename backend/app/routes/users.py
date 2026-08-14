"""
Routes for user-related lookups. Currently just one: letting a dispatcher
fetch the list of registered agents, so they can pick who to assign a new
delivery to.
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime

from app.db.session import get_db
from app.models.user import UserDB, UserRole, UserOut
from app.models.agent_location import AgentLocationDB, AgentLocationUpdate, AgentLocationOut
from app.models.delivery import DeliveryRecordDB
from app.models.push_subscription import PushSubscriptionDB, PushSubscriptionCreate
from app.routes.deliveries import require_dispatcher
from app.routes.auth import get_current_user
from app.services.push import VAPID_PUBLIC_KEY

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/agents", response_model=List[UserOut])
def list_agents(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_dispatcher),
):
    """Dispatcher/admin-only: list agents in the caller's organization, for the assignment dropdown."""
    return db.query(UserDB).filter(
        UserDB.role == UserRole.agent,
        UserDB.org_id == current_user.org_id,
    ).all()


@router.put("/me/location", response_model=AgentLocationOut)
def update_my_location(
    payload: AgentLocationUpdate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    An agent's device calls this periodically (see the "Share my
    location" toggle in AgentDeliveryList.jsx, driven by the browser's
    navigator.geolocation.watchPosition) while they have any active
    delivery. Upserts a single row per agent — only the latest position
    is kept (see agent_location.py for why this isn't a history log).
    """
    if current_user.role != UserRole.agent:
        raise HTTPException(status_code=403, detail="Only agents share live location.")

    existing = db.query(AgentLocationDB).filter(AgentLocationDB.agent_id == current_user.id).first()
    now = datetime.utcnow()
    if existing:
        existing.latitude = payload.latitude
        existing.longitude = payload.longitude
        existing.updated_at = now
    else:
        existing = AgentLocationDB(
            agent_id=current_user.id,
            latitude=payload.latitude,
            longitude=payload.longitude,
            updated_at=now,
        )
        db.add(existing)
    db.commit()
    db.refresh(existing)
    return existing


@router.get("/deliveries/{delivery_id}/agent-location", response_model=AgentLocationOut)
def get_delivery_agent_location(
    delivery_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Fetch the assigned agent's current live location for a specific
    delivery — scoped so only someone who's actually allowed to see this
    delivery (same org staff) can see where the agent is. Customers use
    a separate, differently-scoped route in customer_dashboard.py.
    """
    delivery = db.query(DeliveryRecordDB).filter(DeliveryRecordDB.id == delivery_id).first()
    if not delivery or delivery.org_id != current_user.org_id:
        raise HTTPException(status_code=404, detail="Delivery not found.")

    location = db.query(AgentLocationDB).filter(AgentLocationDB.agent_id == delivery.agent_id).first()
    if not location:
        raise HTTPException(status_code=404, detail="Agent hasn't shared a live location yet.")
    return location


@router.get("/me/push/vapid-public-key")
def get_staff_vapid_public_key():
    """Same VAPID public key as the customer-facing one — it's public by design, safe to expose to any authenticated caller or none at all."""
    return {"public_key": VAPID_PUBLIC_KEY}


@router.post("/me/push/subscribe")
def subscribe_staff_to_push(
    payload: PushSubscriptionCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Saves a browser's push subscription for a staff member (agent,
    dispatcher, or admin) — so an agent gets a real OS-level notification
    the instant they're assigned a delivery, and a dispatcher/admin gets
    one the instant a new unassigned order lands. Same mechanism as the
    customer-facing subscribe endpoint, just keyed by user_id instead of
    customer_id — see models/push_subscription.py.
    """
    existing = db.query(PushSubscriptionDB).filter(PushSubscriptionDB.endpoint == payload.endpoint).first()
    if existing:
        existing.user_id = current_user.id
        existing.customer_id = None
        existing.p256dh = payload.keys.get("p256dh", "")
        existing.auth = payload.keys.get("auth", "")
    else:
        db.add(PushSubscriptionDB(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            endpoint=payload.endpoint,
            p256dh=payload.keys.get("p256dh", ""),
            auth=payload.keys.get("auth", ""),
            created_at=datetime.utcnow(),
        ))
    db.commit()
    return {"message": "Subscribed to push notifications."}
