"""
Routes for delivery records: create, update, list, delete, and role-specific
views (a dispatcher's full list vs. an agent's assigned-to-them list).

These are the "normal" endpoints used when either role is online. The
offline sync flow (bulk upload with conflict resolution) lives separately
in routes/sync.py, since it has different logic (see
docs/TECHNICAL_ARCHITECTURE.md).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.models.delivery import (
    DeliveryRecordDB,
    DeliveryRecordCreate,
    DeliveryRecordUpdate,
    DeliveryRecordOut,
)
from app.models.user import UserDB, UserRole
from app.models.delivery_history import DeliveryHistoryDB, DeliveryHistoryOut
from app.services.history import record_history_entry
from app.routes.auth import get_current_user

router = APIRouter(prefix="/deliveries", tags=["deliveries"])


def require_dispatcher(current_user: UserDB = Depends(get_current_user)) -> UserDB:
    """
    FastAPI dependency that only allows dispatchers through. Used on routes
    that should be dispatcher-only, like creating/assigning a delivery to an
    agent, or viewing the full cross-agent list.
    """
    if current_user.role != UserRole.dispatcher:
        raise HTTPException(status_code=403, detail="Only dispatchers can do this.")
    return current_user


@router.post("/", response_model=DeliveryRecordOut)
def create_delivery(
    record: DeliveryRecordCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_dispatcher),
):
    """
    Create and assign a new delivery record to a specific agent.

    Dispatcher-only: a dispatcher picks which agent (record.agent_id) this
    delivery is assigned to. This replaces the earlier placeholder flow
    where agents self-created "sample" deliveries — deliveries now
    originate from a dispatcher assigning real work.
    """
    existing = db.query(DeliveryRecordDB).filter(DeliveryRecordDB.id == record.id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Delivery record with this ID already exists")

    # Confirm the target agent actually exists and is really an agent
    target_agent = db.query(UserDB).filter(UserDB.id == record.agent_id).first()
    if not target_agent or target_agent.role != UserRole.agent:
        raise HTTPException(status_code=400, detail="The selected agent doesn't exist.")

    db_record = DeliveryRecordDB(**record.model_dump())
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
    """
    db_record = db.query(DeliveryRecordDB).filter(DeliveryRecordDB.id == delivery_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="Delivery record not found")

    old_status = db_record.status

    db_record.status = update.status
    db_record.notes = update.notes
    db_record.location_note = update.location_note
    db_record.updated_at = update.updated_at

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

    return db_record


@router.get("/", response_model=List[DeliveryRecordOut])
def list_deliveries(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_dispatcher),
):
    """List all delivery records across all agents — dispatcher dashboard only."""
    return db.query(DeliveryRecordDB).all()


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
    return db.query(DeliveryRecordDB).filter(DeliveryRecordDB.agent_id == current_user.id).all()


@router.get("/{delivery_id}", response_model=DeliveryRecordOut)
def get_delivery(
    delivery_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get a single delivery record by ID."""
    db_record = db.query(DeliveryRecordDB).filter(DeliveryRecordDB.id == delivery_id).first()
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
    like a timeline.
    """
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
    Delete a delivery record permanently.

    Used when the agent removes a record from their local list — if the
    record was already synced, this also removes it from the server so
    the two stay consistent. If it was never synced (server never had it),
    this simply does nothing on the server side, which is fine.
    """
    db_record = db.query(DeliveryRecordDB).filter(DeliveryRecordDB.id == delivery_id).first()
    if not db_record:
        # Nothing to delete server-side — not an error, since the record
        # may have only ever existed locally (never synced)
        return {"deleted": False, "reason": "not found on server"}

    db.delete(db_record)
    db.commit()
    return {"deleted": True}