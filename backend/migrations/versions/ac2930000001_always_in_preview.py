import sqlalchemy as sa
from alembic import op

revision = "ac2930000001"
down_revision = "ac2870000001"
branch_labels = None
depends_on = None


def upgrade():
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("clothing_items")
    }
    if "always_in_preview" not in existing:
        op.add_column(
            "clothing_items",
            sa.Column("always_in_preview", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade():
    # Keep the additive setting so rollback preserves users' preview choices.
    pass
