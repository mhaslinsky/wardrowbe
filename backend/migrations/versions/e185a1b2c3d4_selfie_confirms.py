"""Store selfie confirmation requests for durable replay."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "e185a1b2c3d4"
down_revision = "e183a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "selfie_confirms",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("worn_on", sa.Date(), nullable=False),
        sa.Column(
            "outfit_id", UUID(as_uuid=True), sa.ForeignKey("outfits.id", ondelete="SET NULL")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "request_id", name="uq_selfie_confirm_request"),
    )


def downgrade() -> None:
    op.drop_table("selfie_confirms")
