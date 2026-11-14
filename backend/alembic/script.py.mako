"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Rules for this file (see docs/MIGRATIONS.md):
  * Use the *_if_missing / *_if_exists helpers from app.db.migration_helpers so the revision is safe on
    databases that were previously kept up to date by create_all().
  * Write a real downgrade(), or raise with an explanation of the rollback plan.
  * Never edit a revision once it has been applied anywhere: add a new one.
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
