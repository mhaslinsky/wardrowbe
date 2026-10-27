import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "e243a1b2c3d4"
down_revision = "e242a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "item_purchases",
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("clothing_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("source_url", sa.String(2000)),
        sa.Column("price", sa.Numeric(10, 2)),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("planned_size", sa.String(100)),
        sa.Column("passed_reason", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("state IN ('considering', 'passed', 'bought')"),
    )


def downgrade():
    op.drop_table("item_purchases")
