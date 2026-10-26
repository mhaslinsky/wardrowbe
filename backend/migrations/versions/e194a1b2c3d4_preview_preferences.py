import sqlalchemy as sa
from alembic import op

revision = "e194a1b2c3d4"
down_revision = "e193a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_preferences",
        sa.Column("preview_preferences", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    op.execute(
        "UPDATE user_preferences SET preview_preferences = jsonb_build_object('lines', '[]'::jsonb, 'tuck', 'outfit', 'rev', gen_random_uuid()::text)"
    )
    op.alter_column("user_preferences", "preview_preferences", nullable=False)
    op.add_column("outfit_previews", sa.Column("preferences_key", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("outfit_previews", "preferences_key")
    op.drop_column("user_preferences", "preview_preferences")
