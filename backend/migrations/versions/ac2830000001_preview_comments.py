import sqlalchemy as sa
from alembic import op

revision = "ac2830000001"
down_revision = "ac2610000001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DELETE FROM preview_feedback WHERE kind = 'like' AND note ~ '^[[:space:]]*$'")
    op.execute("UPDATE preview_feedback SET kind = 'comment' WHERE kind = 'like'")
    op.drop_index("uq_preview_like", table_name="preview_feedback")
    op.create_index(
        "uq_preview_comment",
        "preview_feedback",
        ["user_id", "outfit_id", "preview_created_at"],
        unique=True,
        postgresql_where=sa.text("kind = 'comment'"),
    )


def downgrade():
    op.drop_index("uq_preview_comment", table_name="preview_feedback")
    op.execute("UPDATE preview_feedback SET kind = 'like' WHERE kind = 'comment'")
    op.create_index(
        "uq_preview_like",
        "preview_feedback",
        ["user_id", "outfit_id", "preview_created_at"],
        unique=True,
        postgresql_where=sa.text("kind = 'like'"),
    )
