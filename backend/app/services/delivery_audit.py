"""Writes DeliveryChangeAuditDB rows (see models/delivery_audit.py)."""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.delivery_audit import DeliveryChangeAuditDB


def _v(value):
    return getattr(value, "value", value)


def record_change_decision(
    db: Session,
    *,
    org_id: str,
    delivery_id: str,
    outcome: str,
    source: str,
    actor=None,
    order_id: str | None = None,
    code: str | None = None,
    message: str | None = None,
    base_version: int | None = None,
    version_before: int | None = None,
    version_after: int | None = None,
    status_before=None,
    status_after=None,
    occurred_at: datetime | None = None,
    client_occurred_at: datetime | None = None,
    clock_offset_seconds: float | None = None,
    commit: bool = True,
) -> DeliveryChangeAuditDB:
    """`commit=False` leaves the row pending in the caller's transaction (see services/history.py)."""
    entry = DeliveryChangeAuditDB(
        org_id=org_id,
        delivery_id=delivery_id,
        order_id=order_id,
        outcome=outcome,
        source=source,
        code=code,
        message=message,
        actor_user_id=getattr(actor, "id", None),
        actor_display_name=getattr(actor, "display_name", None),
        actor_role=_v(getattr(actor, "role", None)),
        base_version=base_version,
        version_before=version_before,
        version_after=version_after,
        status_before=_v(status_before),
        status_after=_v(status_after),
        occurred_at=occurred_at,
        client_occurred_at=client_occurred_at,
        clock_offset_seconds=clock_offset_seconds,
        recorded_at=datetime.utcnow(),  # always the server's real time
    )
    db.add(entry)
    if commit:
        db.commit()
    return entry
