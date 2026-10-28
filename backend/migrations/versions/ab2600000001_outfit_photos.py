import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "ab2600000001"
down_revision = "e248a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("outfits", sa.Column("selfie_paths", JSONB, nullable=True))
    op.add_column(
        "outfits",
        sa.Column("card_image_choice", sa.Text(), nullable=False, server_default="automatic"),
    )
    op.create_check_constraint(
        "ck_outfit_card_image_choice",
        "outfits",
        "card_image_choice IN ('automatic', 'photo', 'preview')",
    )


def downgrade():
    op.drop_constraint("ck_outfit_card_image_choice", "outfits", type_="check")
    op.drop_column("outfits", "card_image_choice")
    op.drop_column("outfits", "selfie_paths")
