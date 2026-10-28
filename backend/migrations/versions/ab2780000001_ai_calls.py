import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "ab2780000001"
down_revision = "ab2600000001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        *[
            sa.Column(name, sa.Text(), nullable=False)
            for name in ("feature", "provider", "requested_model", "prompt_sha256", "outcome")
        ],
        *[
            sa.Column(name, sa.Text())
            for name in (
                "resource_type",
                "returned_model",
                "error_kind",
                "fallback_reason",
                "cost_currency",
            )
        ],
        sa.Column("resource_id", sa.Uuid()),
        sa.Column("previous_call_id", sa.Uuid()),
        sa.Column("request_bytes", sa.BigInteger(), nullable=False),
        sa.Column("request_sha256", sa.Text(), nullable=False),
        sa.Column("prompt_bytes", sa.BigInteger(), nullable=False),
        sa.Column("images", JSONB, nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("usage", JSONB),
        sa.Column("reported_cost", sa.Numeric()),
        sa.Column("answer", JSONB),
        sa.CheckConstraint("outcome IN ('success', 'error')", name="ck_ai_calls_outcome"),
    )
    op.create_index("ix_ai_calls_owner_created", "ai_calls", ["user_id", "created_at", "id"])
    op.create_index("ix_ai_calls_owner_feature", "ai_calls", ["user_id", "feature", "created_at"])
    op.create_index(
        "ix_ai_calls_owner_resource", "ai_calls", ["user_id", "resource_type", "resource_id"]
    )


def downgrade():
    op.drop_table("ai_calls")
