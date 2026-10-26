import sqlalchemy as sa
from alembic import op

revision = "e193a1b2c3d4"
down_revision = "e192a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("outfit_previews", sa.Column("clothes_key", sa.Text(), nullable=True))
    op.add_column("outfit_previews", sa.Column("person_key", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("outfit_previews", "person_key")
    op.drop_column("outfit_previews", "clothes_key")
