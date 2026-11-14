"""sync_events: the idempotency ledger for POST /sync

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28

Adds the table that makes offline sync idempotent (models/sync_event.py, services/sync_support.py): one row per
event_id the server has DECIDED about, written in the same transaction as the change itself. The UNIQUE
(actor_user_id, event_id) constraint is what makes two racing requests with the same event safe.

Purely additive: no existing table is touched, no data is read or rewritten.

ROLLBACK PLAN: downgrade drops the table. The only thing lost is the memory of which events were already
applied, so a device that re-sends an old event after a rollback could have it applied a second time — roll back
the application code first (the previous release doesn't use the table), then the schema, and only if needed.
"""
from alembic import op
import sqlalchemy as sa

from app.db.migration_helpers import create_index_if_missing, create_table_if_missing, drop_index_if_exists, drop_table_if_exists

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_sync_events_org_id", ["org_id"]),
    ("ix_sync_events_delivery_id", ["delivery_id"]),
    ("ix_sync_events_created_at", ["created_at"]),
)


def upgrade() -> None:
    create_table_if_missing(
        sa, op, "sync_events",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("delivery_id", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("actor_user_id", "event_id", name="uq_sync_events_actor_event"),
    )
    for name, columns in INDEXES:
        create_index_if_missing(sa, op, name, "sync_events", columns)


def downgrade() -> None:
    for name, _ in INDEXES:
        drop_index_if_exists(sa, op, name, "sync_events")
    drop_table_if_exists(sa, op, "sync_events")
