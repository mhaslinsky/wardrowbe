"""Add trips."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ab1810000001"
down_revision = "e186a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "trips",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("destination", sa.String(200), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("occasions", postgresql.JSONB(), nullable=False),
        sa.Column("days", postgresql.JSONB(), nullable=False),
        sa.Column("packing", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_trips_user_id", "trips", ["user_id"])


def downgrade():
    op.drop_table("trips")
