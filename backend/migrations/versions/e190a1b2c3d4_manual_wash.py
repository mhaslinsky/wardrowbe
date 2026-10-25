import sqlalchemy as sa
from alembic import op

revision = "e190a1b2c3d4"
down_revision = "e189a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "clothing_items",
        sa.Column("manually_needs_wash", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "wash_history",
        sa.Column("manually_needed_wash", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("wash_history", "manually_needed_wash")
    op.drop_column("clothing_items", "manually_needs_wash")
