"""add quantity and clean_count to clothing_items

Revision ID: b8e2f4a6c1d3
Revises: a7d3c9e1f5b2
Create Date: 2026-11-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e2f4a6c1d3"
down_revision: str | None = "a7d3c9e1f5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clothing_items",
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "clothing_items",
        sa.Column("clean_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute("UPDATE clothing_items SET clean_count = 0 WHERE needs_wash")
    op.create_check_constraint(
        "ck_clothing_items_quantity_positive", "clothing_items", "quantity >= 1"
    )
    op.create_check_constraint(
        "ck_clothing_items_clean_count_range",
        "clothing_items",
        "clean_count >= 0 AND clean_count <= quantity",
    )


def downgrade() -> None:
    op.drop_constraint("ck_clothing_items_clean_count_range", "clothing_items", type_="check")
    op.drop_constraint("ck_clothing_items_quantity_positive", "clothing_items", type_="check")
    op.drop_column("clothing_items", "clean_count")
    op.drop_column("clothing_items", "quantity")
