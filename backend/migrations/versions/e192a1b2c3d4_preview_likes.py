import sqlalchemy as sa
from alembic import op

revision = "e192a1b2c3d4"
down_revision = "e191a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("preview_feedback", sa.Column("preview_created_at", sa.DateTime(timezone=True)))
    op.add_column(
        "preview_feedback", sa.Column("paths", sa.JSON(), server_default="{}", nullable=False)
    )
    op.create_index(
        "uq_preview_like",
        "preview_feedback",
        ["user_id", "outfit_id", "preview_created_at"],
        unique=True,
        postgresql_where=sa.text("kind = 'like'"),
    )


def downgrade():
    op.drop_index("uq_preview_like", table_name="preview_feedback")
    op.drop_column("preview_feedback", "paths")
    op.drop_column("preview_feedback", "preview_created_at")
