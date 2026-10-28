import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "ac2710000001"
down_revision = "ab2780000001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_preferences", sa.Column("wash_intervals", JSONB, nullable=True))


def downgrade():
    op.drop_column("user_preferences", "wash_intervals")
