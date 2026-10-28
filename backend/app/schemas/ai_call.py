from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CallFeedback(BaseModel):
    id: UUID
    kind: str
    user_id: UUID
    outfit_id: UUID | None = None
    created_at: datetime
    data: dict[str, Any]


class AICallRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    created_at: datetime
    feature: str
    resource_type: str | None
    resource_id: UUID | None
    previous_call_id: UUID | None
    provider: str
    requested_model: str
    returned_model: str | None
    request_bytes: int
    request_sha256: str
    prompt_bytes: int
    prompt_sha256: str
    images: list
    latency_ms: float
    outcome: str
    error_kind: str | None
    http_status: int | None
    fallback_reason: str | None
    usage: dict | None
    reported_cost: Decimal | None
    cost_currency: str | None
    answer: dict | None
    feedback: list[CallFeedback] = Field(default_factory=list)


class AICallPage(BaseModel):
    items: list[AICallRead]
    next_cursor: str | None = None
