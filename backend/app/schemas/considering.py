from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, UrlConstraints, field_validator

from app.schemas.item import ItemResponse
from app.schemas.outfit import Occasion


class PurchaseFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_url: Annotated[HttpUrl, UrlConstraints(max_length=2000)] | None = None
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


class ConsideringPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    companion_ids: list[UUID] = Field(default_factory=list, max_length=19)

    @field_validator("companion_ids")
    @classmethod
    def unique_companions(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Choose unique companions")
        return value


class ConsideringSuggestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    occasion: Occasion = "casual"
