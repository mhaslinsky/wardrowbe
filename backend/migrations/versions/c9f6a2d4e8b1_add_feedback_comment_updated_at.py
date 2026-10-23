"""Track when outfit notes change."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9f6a2d4e8b1"
down_revision: str | None = "b8e4f1c2d3a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_feedback", sa.Column("comment_updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        sa.text("UPDATE user_feedback SET comment_updated_at = created_at WHERE comment ~ '[^\\s]'")
    )


def downgrade() -> None:
    op.drop_column("user_feedback", "comment_updated_at")
