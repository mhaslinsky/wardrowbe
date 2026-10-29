import sqlalchemy as sa
from alembic import op

revision = "ac2870000001"
down_revision = "ac2830000001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_preferences", sa.Column("active_start_hour", sa.Integer(), nullable=True))
    op.add_column("user_preferences", sa.Column("active_end_hour", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_user_preferences_active_hours",
        "user_preferences",
        "(active_start_hour IS NULL AND active_end_hour IS NULL) OR "
        "(active_start_hour IS NOT NULL AND active_end_hour IS NOT NULL "
        "AND active_start_hour BETWEEN 0 AND 23 AND active_end_hour BETWEEN 0 AND 23 "
        "AND active_end_hour >= active_start_hour + 2)",
    )


def downgrade():
    op.drop_constraint("ck_user_preferences_active_hours", "user_preferences", type_="check")
    op.drop_column("user_preferences", "active_end_hour")
    op.drop_column("user_preferences", "active_start_hour")
