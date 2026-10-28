import sqlalchemy as sa
from alembic import op

revision = "ac2610000001"
down_revision = "ab2720000001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("preview_feedback", sa.Column("comparison_call_id", sa.Uuid(), nullable=True))


def downgrade():
    op.drop_column("preview_feedback", "comparison_call_id")
