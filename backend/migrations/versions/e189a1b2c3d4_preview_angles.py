import sqlalchemy as sa
from alembic import op

revision = "e189a1b2c3d4"
down_revision = "e187a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE preview_photos SET paths = jsonb_build_object('front', paths)")


def downgrade():
    from app.models.preview import delete_preview_files

    connection = op.get_bind()
    for paths in connection.execute(sa.text("SELECT paths FROM preview_photos")).scalars():
        for angle in ("quarter", "side"):
            delete_preview_files(paths.get(angle, {}), strict=True)
    op.execute("DELETE FROM preview_photos WHERE NOT (paths ? 'front')")
    op.execute("UPDATE preview_photos SET paths = paths->'front'")
