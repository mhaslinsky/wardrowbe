from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

FactKind = Literal["brand", "line", "size", "type", "color", "feel", "size_rule", "rating", "fiber"]
FactOrigin = Literal["explicit_note", "stored_tag", "profile_row", "unknown"]
ValueText = Annotated[str, Field(max_length=300)]


class EvidenceSource(BaseModel):
    item_id: str | None = None
    profile_rev: str | None = None
    row_key: str | None = None
    field: str
    excerpt: str | None = Field(None, max_length=300)
    truncated: bool = False


class EvidenceFact(BaseModel):
    fact_id: str
    source_id: str
    kind: FactKind
    origin: FactOrigin
    producer: Literal["user", "ai", "unknown"] = "unknown"
    status: Literal["known", "unknown", "conflict"]
    value: dict[str, ValueText | None] | None
    conflict_ids: list[str] = Field(default_factory=list)


class FitEvidence(BaseModel):
    version: Literal[1] = 1
    rules_version: Literal["fit-evidence-1"] = "fit-evidence-1"
    anchor_id: str
    profile_rev: str
    notes_truncated: bool = False
    sources: dict[str, EvidenceSource]
    facts: list[EvidenceFact]
    size: dict[str, Any]
    feel: dict[str, Any]
    duplicates: dict[str, Any]
    skip: dict[str, Any]
    outfit_ideas_found: dict[str, Any]
    selection: dict[str, Any]
