from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e186a1b2c3d4"
down_revision: str | None = "c9f6a2d4e8b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "schedules", sa.Column("notify", sa.Boolean(), server_default=sa.true(), nullable=False)
    )


def downgrade() -> None:
    op.drop_column("schedules", "notify")
