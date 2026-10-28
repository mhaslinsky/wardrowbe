from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.fit_evidence import EvidenceSource


class BuyCheckCitation(EvidenceSource):
    source_id: str
    quote: str | None = None
    value: int | None = None


class BuyCheckReason(BaseModel):
    kind: Literal[
        "fit_and_cut",
        "feel_and_quality",
        "style_and_look",
        "few_outfits",
        "skip",
        "wrong_size",
        "duplicate",
    ]
    citations: list[BuyCheckCitation] = Field(min_length=1)


class BuyCheckResponse(BaseModel):
    status: Literal["scored", "insufficient", "skip", "wrong_size", "duplicate", "error"]
    percentage: int | None = Field(None, ge=0, le=100)
    pluses: list[BuyCheckReason] = Field(default_factory=list)
    minuses: list[BuyCheckReason] = Field(default_factory=list)
    unknowns: list[BuyCheckReason] = Field(default_factory=list)
    outfits: dict[str, Any]
    model_used: str | None = Field(None, max_length=120)
    requested_model: str | None = None
    fallback_reason: str | None = None
    near_duplicate: bool = False
    error: Literal["missing_key", "disabled", "unavailable"] | None = None
    notice_status: (
        Literal["sent", "failed", "failed_release", "throttled", "not_configured"] | None
    ) = None
