import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SelfieConfirm(Base):
    __tablename__ = "selfie_confirms"
    __table_args__ = (UniqueConstraint("user_id", "request_id", name="uq_selfie_confirm_request"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    payload_hash: Mapped[str] = mapped_column(Text)
    worn_on: Mapped[date] = mapped_column(Date)
    outfit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("outfits.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
