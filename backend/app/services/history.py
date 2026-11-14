"""
Shared helper for writing a delivery history entry. Used from three places:
creating a delivery (deliveries.py), updating one online (deliveries.py),
and applying a conflict-resolved change from offline sync (conflict_resolver.py).
Centralizing this in one function keeps the history log consistent no
matter which path caused the change.
"""

from sqlalchemy.orm import Session
from app.models.delivery_history import DeliveryHistoryDB


def record_history_entry(
    db: Session,
    delivery_id: str,
    changed_by_user_id: str,
    changed_by_display_name: str,
    old_status,
    new_status,
    changed_at,
    note: str = None,
    commit: bool = True,
):
    """`commit=False` leaves the entry pending in the caller's transaction, so it is saved (or discarded)
    together with the change it describes — used by the offline-sync path, where the delivery update, this
    history entry, the attempt and the audit row must all-or-nothing (services/conflict_resolver.py)."""
    entry = DeliveryHistoryDB(
        delivery_id=delivery_id,
        changed_by_user_id=changed_by_user_id,
        changed_by_display_name=changed_by_display_name,
        old_status=old_status.value if hasattr(old_status, "value") else old_status,
        new_status=new_status.value if hasattr(new_status, "value") else new_status,
        changed_at=changed_at,
        note=note,
    )
    db.add(entry)
    if commit:
        db.commit()
    return entry
