"""
Conflict resolution logic for syncing offline delivery records.

Strategy: last-write-wins based on `updated_at` timestamp.
See docs/TECHNICAL_ARCHITECTURE.md for the full reasoning and known
limitations of this approach.
"""

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.delivery import DeliveryRecordDB
from app.models.user import UserDB
from app.services.history import record_history_entry
from app.services.notifications import notify_customer_of_status_change


def _normalize_to_naive_utc(dt: datetime) -> datetime:
    """
    Browsers send timestamps like '2026-07-22T19:10:46.276Z', which Python
    parses as timezone-AWARE (they carry a 'this is UTC' marker). But
    SQLite/SQLAlchemy stores and returns timezone-NAIVE datetimes (no
    marker at all). Python refuses to compare aware and naive datetimes
    directly, which is exactly the bug this caused.

    Fix: always convert to UTC first (in case it wasn't already), then
    strip the timezone marker, so every datetime we compare or store is
    naive UTC consistently.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def resolve_and_apply(record_data: dict, db: Session) -> DeliveryRecordDB:
    """
    Given an incoming record from a client's offline sync batch, decide
    whether to insert it, update the existing one, or discard it (because
    the server's existing version is newer).

    Returns the FINAL resolved record as stored in the database — the
    client should overwrite its local copy with this, in case the server's
    version won the conflict.

    Raises ValueError if the record's agent_id doesn't match a real user —
    the caller (routes/sync.py) catches this per-record so one bad record
    in a batch can't take down the whole sync request.
    """
    # Normalize incoming timestamps immediately so everything downstream
    # (comparisons AND storage) is consistently naive UTC.
    record_data["created_at"] = _normalize_to_naive_utc(record_data["created_at"])
    record_data["updated_at"] = _normalize_to_naive_utc(record_data["updated_at"])

    existing = db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.id == record_data["id"]
    ).first()

    # /sync is intentionally left unauthenticated (see docs/SECURITY_AND_ACCESS.md),
    # so history entries from this path are attributed to the record's
    # assigned agent (looked up by ID) rather than a logged-in request user.
    agent = db.query(UserDB).filter(UserDB.id == record_data["agent_id"]).first()
    if not agent:
        raise ValueError(f"No user found matching agent_id '{record_data['agent_id']}'.")
    agent_name = agent.display_name

    # SECURITY: org_id is NEVER trusted from the client — since /sync has
    # no authentication, a client payload could otherwise claim any org_id
    # it wants. It's always overwritten here with the agent's own real
    # organization, derived server-side from the trusted DB lookup above.
    # This makes it impossible for a delivery to end up tagged to a
    # different organization than the agent it's assigned to, regardless
    # of what a sync payload claims.
    record_data["org_id"] = agent.org_id

    # SECURITY (cross-tenant write protection): if a record with this ID
    # already exists, it must belong to the SAME organization as the
    # agent making this sync request. Without this check, since /sync has
    # no authentication, a client could send an existing delivery ID from
    # a DIFFERENT organization paired with one of its own agent_ids, and
    # this code would otherwise happily overwrite another company's
    # delivery data. Treating this as a failed record (not a silent
    # skip, and not a crash) makes the rejection visible in the sync
    # response's `errors` list rather than disappearing quietly.
    if existing and existing.org_id != agent.org_id:
        raise ValueError(
            "This delivery ID belongs to a different organization and cannot be modified."
        )

    if not existing:
        # No conflict — this is a new record, just insert it
        new_record = DeliveryRecordDB(**record_data)
        db.add(new_record)
        db.commit()
        db.refresh(new_record)

        record_history_entry(
            db,
            delivery_id=new_record.id,
            changed_by_user_id=record_data["agent_id"],
            changed_by_display_name=agent_name,
            old_status=None,
            new_status=new_record.status,
            changed_at=new_record.created_at,
            note="Created via offline sync",
        )
        return new_record

    # Conflict case: record already exists on the server.
    # Compare timestamps — whichever is later wins. Both sides are now
    # guaranteed naive UTC, so this comparison is safe.
    incoming_updated_at = record_data["updated_at"]
    if incoming_updated_at > existing.updated_at:
        # Incoming (client) change is newer — apply it
        old_status = existing.status
        existing.status = record_data["status"]
        existing.notes = record_data.get("notes")
        existing.location_note = record_data.get("location_note")
        existing.updated_at = incoming_updated_at
        if record_data.get("proof_of_delivery") is not None:
            existing.proof_of_delivery = record_data.get("proof_of_delivery")
        db.commit()
        db.refresh(existing)

        if old_status != existing.status:
            record_history_entry(
                db,
                delivery_id=existing.id,
                changed_by_user_id=record_data["agent_id"],
                changed_by_display_name=agent_name,
                old_status=old_status,
                new_status=existing.status,
                changed_at=incoming_updated_at,
                note="Updated via offline sync",
            )
            notify_customer_of_status_change(
                db,
                delivery_id=existing.id,
                order_id=existing.order_id,
                new_status=existing.status.value if hasattr(existing.status, "value") else existing.status,
                customer_email=existing.customer_email,
                customer_phone=existing.customer_phone,
                customer_id=existing.customer_id,
            )
        return existing
    else:
        # Server's existing version is newer or equal — keep it, discard incoming
        return existing

