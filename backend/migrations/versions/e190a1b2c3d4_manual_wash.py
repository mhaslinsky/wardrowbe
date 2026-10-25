import sqlalchemy as sa
from alembic import op

revision = "e190a1b2c3d4"
down_revision = "e189a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    for table, column in (
        ("clothing_items", "manually_needs_wash"),
        ("wash_history", "manually_needed_wash"),
    ):
        existing = {entry["name"] for entry in sa.inspect(op.get_bind()).get_columns(table)}
        if column not in existing:
            op.add_column(
                table, sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.false())
            )


def downgrade():
    # Keep additive columns so rollback preserves manual flags and current ORM inserts still work.
    pass
