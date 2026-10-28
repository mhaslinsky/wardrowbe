import sqlalchemy as sa
from alembic import op

revision = "ab2720000001"
down_revision = "ab2780000001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("preview_feedback", sa.Column("ai_call_id", sa.Uuid(), nullable=True))
    op.create_index("ix_preview_feedback_ai_call_id", "preview_feedback", ["ai_call_id"])


def downgrade():
    op.drop_index("ix_preview_feedback_ai_call_id", table_name="preview_feedback")
    op.drop_column("preview_feedback", "ai_call_id")
