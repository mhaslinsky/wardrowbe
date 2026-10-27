from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.schemas.item import ItemResponse


class PurchaseFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_url: HttpUrl | None = Field(None, max_length=2000)
    price: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2)
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    planned_size: str | None = Field(None, max_length=100)


class ConsideringCreate(PurchaseFields):
    image_urls: list[HttpUrl] = Field(min_length=1, max_length=5)
    name: str | None = Field(None, max_length=100)
    brand: str | None = Field(None, max_length=100)
    type: str = Field("unknown", min_length=1, max_length=50)


class ConsideringEdit(PurchaseFields):
    name: str | None = Field(None, max_length=100)


class PassRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)


class ConsideringResponse(PurchaseFields):
    state: Literal["considering", "passed", "bought"]
    passed_reason: str | None
    item: ItemResponse
