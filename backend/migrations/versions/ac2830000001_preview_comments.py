import sqlalchemy as sa
from alembic import op

revision = "ac2830000001"
down_revision = "ac2610000001"
branch_labels = None
depends_on = None


def upgrade():
    from app.models.preview import delete_preview_files, preview_file_paths

    connection = op.get_bind()
    retained_paths = {
        path
        for paths in connection.execute(sa.text("SELECT paths FROM outfit_previews")).scalars()
        for path in preview_file_paths(paths)
    }
    deleted_ids = []
    deleted_paths = set()
    for feedback in connection.execute(
        sa.text("SELECT id, kind, note, paths FROM preview_feedback")
    ).mappings():
        if feedback["kind"] == "like" and not feedback["note"].strip():
            deleted_ids.append(feedback["id"])
            deleted_paths.update(preview_file_paths(feedback["paths"]))
        else:
            retained_paths.update(preview_file_paths(feedback["paths"]))
    # Empty likes can retain drawings that were replaced.
    orphaned = {path: path for path in deleted_paths if path not in retained_paths}
    if deleted_ids:
        connection.execute(
            sa.text("DELETE FROM preview_feedback WHERE id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": deleted_ids},
        )
    op.execute("UPDATE preview_feedback SET kind = 'comment' WHERE kind = 'like'")
    op.drop_index("uq_preview_like", table_name="preview_feedback")
    op.create_index(
        "uq_preview_comment",
        "preview_feedback",
        ["user_id", "outfit_id", "preview_created_at"],
        unique=True,
        postgresql_where=sa.text("kind = 'comment'"),
    )
    delete_preview_files(orphaned, strict=True)


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
