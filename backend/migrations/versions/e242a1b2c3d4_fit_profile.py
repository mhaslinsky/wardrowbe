import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "e242a1b2c3d4"
down_revision = "e194a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_fit_profiles",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("profile", JSONB(), nullable=False),
    )


def downgrade():
    op.drop_table("user_fit_profiles")
