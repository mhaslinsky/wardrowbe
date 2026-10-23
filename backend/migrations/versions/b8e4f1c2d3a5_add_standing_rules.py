"""Add per-user standing outfit rules."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b8e4f1c2d3a5"
down_revision: str | None = "a7d3c9e1f5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_preferences",
        sa.Column("standing_rules", JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("user_preferences", "standing_rules")
