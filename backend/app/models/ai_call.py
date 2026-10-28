from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AICall(Base):
    __tablename__ = "ai_calls"
    __table_args__ = (
        CheckConstraint("outcome IN ('success', 'error')", name="ck_ai_calls_outcome"),
        Index("ix_ai_calls_owner_created", "user_id", "created_at", "id"),
        Index("ix_ai_calls_owner_feature", "user_id", "feature", "created_at"),
        Index("ix_ai_calls_owner_resource", "user_id", "resource_type", "resource_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    feature: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[UUID | None]
    previous_call_id: Mapped[UUID | None]
    provider: Mapped[str] = mapped_column(Text)
    requested_model: Mapped[str] = mapped_column(Text)
    returned_model: Mapped[str | None] = mapped_column(Text)
    request_bytes: Mapped[int] = mapped_column(BigInteger)
    request_sha256: Mapped[str] = mapped_column(Text)
    prompt_bytes: Mapped[int] = mapped_column(BigInteger)
    prompt_sha256: Mapped[str] = mapped_column(Text)
    images: Mapped[list] = mapped_column(JSONB)
    latency_ms: Mapped[float] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(Text)
    error_kind: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None]
    fallback_reason: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict | None] = mapped_column(JSONB)
    reported_cost: Mapped[Decimal | None] = mapped_column(Numeric)
    cost_currency: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[dict | None] = mapped_column(JSONB)
