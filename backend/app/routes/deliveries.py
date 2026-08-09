"""
Routes for delivery records: create, update, list, delete, and role-specific
views (a dispatcher's full list vs. an agent's assigned-to-them list).

Every query in this file filters by `current_user.org_id` — this is what
makes multi-tenant isolation actually hold: two organizations sharing the
same deployment never see each other's deliveries, even though they're
all rows in the same physical `deliveries` table.

The offline sync flow (bulk upload with conflict resolution) lives
separately in routes/sync.py, since it has different logic (see
docs/TECHNICAL_ARCHITECTURE.md).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime
from pydantic import BaseModel

from app.db.session import get_db
from app.models.delivery import (
    DeliveryRecordDB,
    DeliveryRecordCreate,
    DeliveryRecordUpdate,
    DeliveryRecordOut,
    DeliveryStatus,
)
from app.models.user import UserDB, UserRole
from app.models.customer import CustomerDB
from app.models.delivery_history import DeliveryHistoryDB, DeliveryHistoryOut
from app.services.history import record_history_entry
from app.services.notifications import notify_customer_of_status_change
from app.routes.auth import get_current_user

router = APIRouter(prefix="/deliveries", tags=["deliveries"])


def require_dispatcher(current_user: UserDB = Depends(get_current_user)) -> UserDB:
    """
    FastAPI dependency allowing dispatchers AND admins through (an admin
    has a superset of dispatcher permissions within their own
    organization). Used on routes like creating/assigning a delivery, or
    viewing the full cross-agent list.
    """
    if current_user.role not in (UserRole.dispatcher, UserRole.admin):
        raise HTTPException(status_code=403, detail="Only dispatchers or admins can do this.")
    return current_user


@router.post("/", response_model=DeliveryRecordOut)
def create_delivery(
    record: DeliveryRecordCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_dispatcher),
):
    """
    Create and assign a new delivery record to a specific agent.

    Dispatcher/admin-only: picks which agent (record.agent_id) this
    delivery is assigned to. The agent must belong to the SAME
    organization as the person assigning it — org_id is set here from
    the current user's own organization, never trusted from client input,
    so a dispatcher can never (even accidentally) create a delivery
    tagged to a different organization.
    """
    existing = db.query(DeliveryRecordDB).filter(DeliveryRecordDB.id == record.id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Delivery record with this ID already exists")

    # Confirm the target agent actually exists, is really an agent, AND
    # belongs to the same organization as the person assigning this
    target_agent = db.query(UserDB).filter(
        UserDB.id == record.agent_id,
        UserDB.org_id == current_user.org_id,
    ).first()
    if not target_agent or target_agent.role != UserRole.agent:
        raise HTTPException(status_code=400, detail="The selected agent doesn't exist in your organization.")

    db_record = DeliveryRecordDB(**record.model_dump(), org_id=current_user.org_id)

    # Link to a real customer account if one exists matching this email —
    # that's what makes this delivery show up in that customer's logged-in
    # dashboard, not just via a one-off tracking link.
    if db_record.customer_email:
        matching_customer = db.query(CustomerDB).filter(CustomerDB.email == db_record.customer_email).first()
        if matching_customer:
            db_record.customer_id = matching_customer.id

    db.add(db_record)
    db.commit()
    db.refresh(db_record)

    record_history_entry(
        db,
        delivery_id=db_record.id,
        changed_by_user_id=current_user.id,
        changed_by_display_name=current_user.display_name,
        old_status=None,
        new_status=db_record.status,
        changed_at=db_record.created_at,
        note=f"Created and assigned to {target_agent.display_name}",
    )
    notify_customer_of_status_change(
        db,
        delivery_id=db_record.id,
        order_id=db_record.order_id,
        new_status="confirmed",
        customer_email=db_record.customer_email,
        customer_phone=db_record.customer_phone,
        customer_id=db_record.customer_id,
    )

    return db_record


@router.get("/unassigned", response_model=List[DeliveryRecordOut])
def list_unassigned_deliveries(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_dispatcher),
):
    """
    Orders placed by customers through checkout, paid for, but not yet
    assigned to an agent — the dispatcher's "new orders" queue. Manually
    dispatcher-created deliveries never land here since they always pick
    an agent at creation time.
    """
    return db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.org_id == current_user.org_id,
        DeliveryRecordDB.status == DeliveryStatus.pending,
    ).all()


class AssignAgentRequest(BaseModel):
    agent_id: str


@router.patch("/{delivery_id}/assign-agent", response_model=DeliveryRecordOut)
def assign_agent_to_delivery(
    delivery_id: str,
    payload: AssignAgentRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_dispatcher),
):
    """Dispatcher assigns a pending (checkout-created) order to an agent, moving it into the normal fulfillment flow."""
    db_record = db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.id == delivery_id,
        DeliveryRecordDB.org_id == current_user.org_id,
    ).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="Delivery record not found")

    target_agent = db.query(UserDB).filter(
        UserDB.id == payload.agent_id,
        UserDB.org_id == current_user.org_id,
    ).first()
    if not target_agent or target_agent.role != UserRole.agent:
        raise HTTPException(status_code=400, detail="The selected agent doesn't exist in your organization.")

    old_status = db_record.status
    db_record.agent_id = payload.agent_id
    db_record.status = DeliveryStatus.picked_up
    db_record.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_record)

    record_history_entry(
        db,
        delivery_id=db_record.id,
        changed_by_user_id=current_user.id,
        changed_by_display_name=current_user.display_name,
        old_status=old_status,
        new_status=db_record.status,
        changed_at=db_record.updated_at,
        note=f"Assigned to {target_agent.display_name}",
    )
    notify_customer_of_status_change(
        db,
        delivery_id=db_record.id,
        order_id=db_record.order_id,
        new_status=db_record.status.value,
        customer_email=db_record.customer_email,
        customer_phone=db_record.customer_phone,
        customer_id=db_record.customer_id,
    )
    return db_record


@router.patch("/{delivery_id}", response_model=DeliveryRecordOut)
def update_delivery(
    delivery_id: str,
    update: DeliveryRecordUpdate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Update an existing delivery record's status. Any logged-in user can
    call this (an agent updating their own delivery, in the normal online
    flow — offline updates go through the /sync batch endpoint instead).
    Scoped to the caller's own organization, so a user from one
    organization can never update a delivery belonging to another.
    """
    db_record = db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.id == delivery_id,
        DeliveryRecordDB.org_id == current_user.org_id,
    ).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="Delivery record not found")

    old_status = db_record.status

    db_record.status = update.status
    db_record.notes = update.notes
    db_record.location_note = update.location_note
    db_record.updated_at = update.updated_at
    if update.proof_of_delivery is not None:
        db_record.proof_of_delivery = update.proof_of_delivery

    db.commit()
    db.refresh(db_record)

    if old_status != update.status:
        record_history_entry(
            db,
            delivery_id=db_record.id,
            changed_by_user_id=current_user.id,
            changed_by_display_name=current_user.display_name,
            old_status=old_status,
            new_status=update.status,
            changed_at=update.updated_at,
        )
        notify_customer_of_status_change(
            db,
            delivery_id=db_record.id,
            order_id=db_record.order_id,
            new_status=update.status.value,
            customer_email=db_record.customer_email,
            customer_phone=db_record.customer_phone,
            customer_id=db_record.customer_id,
        )

    return db_record


@router.get("/", response_model=List[DeliveryRecordOut])
def list_deliveries(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_dispatcher),
):
    """List all delivery records within the caller's organization — dispatcher/admin dashboard only."""
    return db.query(DeliveryRecordDB).filter(DeliveryRecordDB.org_id == current_user.org_id).all()


@router.get("/mine", response_model=List[DeliveryRecordOut])
def list_my_deliveries(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    List deliveries assigned to the currently logged-in agent. This is what
    the Agent view "pulls" from the server, so dispatcher-assigned
    deliveries actually show up in the agent's local (IndexedDB) list —
    without this, an agent would only ever see deliveries they created
    themselves, never ones assigned to them by a dispatcher.
    """
    return db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.agent_id == current_user.id,
        DeliveryRecordDB.org_id == current_user.org_id,
    ).all()


@router.get("/{delivery_id}", response_model=DeliveryRecordOut)
def get_delivery(
    delivery_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get a single delivery record by ID, scoped to the caller's organization."""
    db_record = db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.id == delivery_id,
        DeliveryRecordDB.org_id == current_user.org_id,
    ).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="Delivery record not found")
    return db_record


@router.get("/{delivery_id}/history", response_model=List[DeliveryHistoryOut])
def get_delivery_history(
    delivery_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Returns the full audit trail for a delivery — every status change,
    who made it, and when — ordered oldest first so it reads top-to-bottom
    like a timeline. Confirms the delivery belongs to the caller's
    organization before returning any history for it.
    """
    db_record = db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.id == delivery_id,
        DeliveryRecordDB.org_id == current_user.org_id,
    ).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="Delivery record not found")

    return (
        db.query(DeliveryHistoryDB)
        .filter(DeliveryHistoryDB.delivery_id == delivery_id)
        .order_by(DeliveryHistoryDB.changed_at.asc())
        .all()
    )


@router.delete("/{delivery_id}")
def delete_delivery(
    delivery_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Delete a delivery record permanently, scoped to the caller's
    organization.

    Used when the agent removes a record from their local list — if the
    record was already synced, this also removes it from the server so
    the two stay consistent. If it was never synced (server never had it),
    this simply does nothing on the server side, which is fine.
    """
    db_record = db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.id == delivery_id,
        DeliveryRecordDB.org_id == current_user.org_id,
    ).first()
    if not db_record:
        # Nothing to delete server-side — not an error, since the record
        # may have only ever existed locally (never synced)
        return {"deleted": False, "reason": "not found on server"}

    db.delete(db_record)
    db.commit()
    return {"deleted": True}
