"""Retain original and redrawn photos."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e183a0b1c2d3"
down_revision = "c9f6a2d4e8b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clothing_items", sa.Column("photo_redraw", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("clothing_items", "photo_redraw")
