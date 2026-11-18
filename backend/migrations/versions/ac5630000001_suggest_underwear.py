import sqlalchemy as sa
from alembic import op

revision = "ac5630000001"
down_revision = "ac5620000001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_preferences",
        sa.Column("suggest_underwear", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade():
    op.drop_column("user_preferences", "suggest_underwear")
