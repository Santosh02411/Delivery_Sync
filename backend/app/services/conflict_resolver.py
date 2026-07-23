"""
Conflict resolution logic for syncing offline delivery records.

Strategy: last-write-wins based on `updated_at` timestamp.
See docs/TECHNICAL_ARCHITECTURE.md for the full reasoning and known
limitations of this approach.
"""

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.delivery import DeliveryRecordDB


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
    """
    # Normalize incoming timestamps immediately so everything downstream
    # (comparisons AND storage) is consistently naive UTC.
    record_data["created_at"] = _normalize_to_naive_utc(record_data["created_at"])
    record_data["updated_at"] = _normalize_to_naive_utc(record_data["updated_at"])

    existing = db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.id == record_data["id"]
    ).first()

    if not existing:
        # No conflict — this is a new record, just insert it
        new_record = DeliveryRecordDB(**record_data)
        db.add(new_record)
        db.commit()
        db.refresh(new_record)
        return new_record

    # Conflict case: record already exists on the server.
    # Compare timestamps — whichever is later wins. Both sides are now
    # guaranteed naive UTC, so this comparison is safe.
    incoming_updated_at = record_data["updated_at"]
    if incoming_updated_at > existing.updated_at:
        # Incoming (client) change is newer — apply it
        existing.status = record_data["status"]
        existing.notes = record_data.get("notes")
        existing.location_note = record_data.get("location_note")
        existing.updated_at = incoming_updated_at
        db.commit()
        db.refresh(existing)
        return existing
    else:
        # Server's existing version is newer or equal — keep it, discard incoming
        return existing

