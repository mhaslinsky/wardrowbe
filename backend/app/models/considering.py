from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    SQLColumnExpression,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from app.database import Base
from app.models.item import ClothingItem
from app.models.outfit import Outfit, OutfitItem

PurchaseState = Literal["considering", "passed", "bought"]


class ItemPurchase(Base):
    __tablename__ = "item_purchases"
    __table_args__ = (CheckConstraint("state IN ('considering', 'passed', 'bought')"),)

    item_id: Mapped[UUID] = mapped_column(
        ForeignKey("clothing_items.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[PurchaseState] = mapped_column(String(16), default="considering")
    source_url: Mapped[str | None] = mapped_column(String(2000))
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    planned_size: Mapped[str | None] = mapped_column(String(100))
    passed_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def owned_item(
    item_id: SQLColumnExpression[UUID] = ClothingItem.id,
) -> ColumnElement[bool]:
    return (
        ~select(ItemPurchase.item_id)
        .where(
            ItemPurchase.item_id == item_id,
            ItemPurchase.state.in_(("considering", "passed")),
        )
        .exists()
    )


def owned_outfit() -> ColumnElement[bool]:
    return ~Outfit.items.any(~owned_item(OutfitItem.item_id))
