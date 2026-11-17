import sqlalchemy as sa
from alembic import op

revision = "ac5620000001"
down_revision = "ac2930000001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_preferences", sa.Column("options_per_day", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_user_preferences_options_per_day", "user_preferences", "options_per_day BETWEEN 1 AND 5"
    )


def downgrade():
    op.drop_constraint("ck_user_preferences_options_per_day", "user_preferences", type_="check")
    op.drop_column("user_preferences", "options_per_day")
