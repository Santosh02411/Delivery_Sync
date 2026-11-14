"""baseline: the schema as it was when Alembic was adopted (release 71)

Revision ID: 0001
Revises:
Create Date: 2026-10-28

The starting point of version-controlled migrations. The schema it creates is the FROZEN description in
app/db/baseline_schema.py (generated mechanically from the models by alembic/tools/freeze_baseline.py).

  * Fresh database  -> every table, column, index and constraint is created.
  * Existing database, created by the old create_all() + lightweight migrator -> nothing is dropped or altered;
    any table/column/index it lacks is added (new columns nullable, exactly like the old startup migrator did),
    so it simply joins the history at this revision with all its data intact. There is no need to `stamp`.

ROLLBACK PLAN: this revision has no downgrade, deliberately — "undo the baseline" would mean dropping every
table. To go back, restore the backup taken before the upgrade (docs/MIGRATIONS.md).
"""
from alembic import op
import sqlalchemy as sa

from app.db.baseline_schema import BASELINE_TABLES
from app.db.migration_helpers import apply_schema

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply_schema(sa, op, BASELINE_TABLES)


def downgrade() -> None:
    raise RuntimeError(
        "The baseline cannot be downgraded: that would drop every table. "
        "Restore the pre-upgrade backup instead (see docs/MIGRATIONS.md)."
    )
