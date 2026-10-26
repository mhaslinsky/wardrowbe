import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "e191a1b2c3d4"
down_revision = "e190a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "preview_feedback",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("user_id", UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "outfit_id", UUID(), sa.ForeignKey("outfits.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade():
    op.drop_table("preview_feedback")
