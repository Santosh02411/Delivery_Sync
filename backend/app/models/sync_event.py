"""
SyncEventDB — the idempotency ledger for POST /sync.

Every offline edit on a device carries an `event_id` (a UUID the device generates once, when the agent makes
the edit, and re-sends unchanged on every retry). When the server DECIDES something about that event —
applies it, reconciles it, or refuses it as a conflict — it writes one row here IN THE SAME TRANSACTION as
the change itself. If the very same event arrives again (the response was lost, the device retried, a
duplicate request raced the first), the stored outcome is replayed instead of the change being applied twice.

What is deliberately NOT recorded: refusals that are not a decision about the change itself —
authorization failures, validation errors, "proof of delivery required", concurrent-update retries. Those
depend on state that can change (a delivery gets reassigned back, POD gets uploaded), so a retry must be
evaluated afresh.

The UNIQUE (actor_user_id, event_id) constraint is what makes this safe under concurrency: of two requests
racing with the same event, exactly one can insert the row; the other fails, rolls back, and replays.
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Text, UniqueConstraint

from app.db.session import Base


class SyncEventDB(Base):
    __tablename__ = "sync_events"
    __table_args__ = (
        UniqueConstraint("actor_user_id", "event_id", name="uq_sync_events_actor_event"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id = Column(String, index=True, nullable=False)
    actor_user_id = Column(String, nullable=False)
    event_id = Column(String(128), nullable=False)
    delivery_id = Column(String, index=True, nullable=False)

    # "applied" | "reconciled" | "already_applied" | "rejected"
    outcome = Column(String, nullable=False)
    # JSON: what is needed to replay the answer (e.g. the structured conflict for a rejection).
    result_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
