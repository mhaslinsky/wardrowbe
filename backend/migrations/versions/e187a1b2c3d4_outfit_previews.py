import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "e187a1b2c3d4"
down_revision = "e185a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "preview_photos",
        sa.Column(
            "user_id", UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("paths", JSONB(), nullable=False),
    )
    op.create_table(
        "outfit_previews",
        sa.Column(
            "outfit_id", UUID(), sa.ForeignKey("outfits.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("user_id", UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("paths", JSONB(), nullable=False),
        sa.Column("usage", JSONB(), nullable=False),
        sa.Column("claim_id", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_outfit_previews_user_id", "outfit_previews", ["user_id"])


def downgrade():
    from app.models.preview import delete_preview_files

    connection = op.get_bind()
    for table in ("outfit_previews", "preview_photos"):
        for paths in connection.execute(sa.text(f"SELECT paths FROM {table}")).scalars():
            delete_preview_files(paths, strict=True)
    op.drop_table("outfit_previews")
    op.drop_table("preview_photos")
