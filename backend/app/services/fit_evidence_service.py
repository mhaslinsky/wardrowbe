from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.considering import ItemPurchase, owned_item
from app.models.item import ClothingItem, ItemStatus
from app.schemas.fit_evidence import FitEvidence
from app.schemas.fit_profile import FitProfile
from app.services.fit_evidence_outfits import outfit_ideas, outfit_pool
from app.services.fit_evidence_parser import EvidenceRecord, normalize, parse_item, parse_row
from app.services.fit_profile_service import FitProfileService


def known_values(record: EvidenceRecord, kind: str) -> set[str]:
    return {
        fact.value["normalized"]
        for fact in record.of_kind(kind)
        if fact.status == "known" and fact.value is not None
    }


def match_fields(record: EvidenceRecord, required: dict[str, str]) -> str:
    for kind, wanted in required.items():
        if any(value != normalize(wanted) for value in known_values(record, kind)):
            return "no_match"
    if (
        any(
            not record.values(kind) or any(fact.status != "known" for fact in record.of_kind(kind))
            for kind in required
        )
        or not required
    ):
        return "unresolved"
    return "match"


def fact_ids(record: EvidenceRecord, kind: str | None = None) -> list[str]:
    return [fact.fact_id for fact in record.facts if kind is None or fact.kind == kind]


def quoted_fiber_notes(records: list[EvidenceRecord]) -> list[dict[str, Any]]:
    notes = []
    for record in records:
        source_ids = {
            fact.source_id
            for fact in record.of_kind("fiber")
            if fact.origin == "explicit_note"
            and fact.value is not None
            and fact.value["percent"] is None
        }
        for source_id in sorted(source_ids):
            unresolved = any(
                fact.status != "known"
                for fact in record.of_kind("fiber")
                if fact.source_id == source_id
            )
            notes.append(
                {
                    "source_id": source_id,
                    "quote": record.sources[source_id].excerpt,
                    "status": "unresolved" if unresolved else "known",
                    **({"display": "can't tell"} if unresolved else {}),
                }
            )
    return notes


def compare(anchor: EvidenceRecord, reference: EvidenceRecord, field: str) -> dict[str, Any]:
    if field == "fiber":
        first, second = anchor.composition(), reference.composition()
        status = (
            "unresolved"
            if first is None or second is None
            else "match"
            if first == second
            else "no_match"
        )

        def selected(record: EvidenceRecord) -> list[str]:
            return [fact.source_id for fact in record.of_kind("fiber")]
    else:

        def selected(record: EvidenceRecord) -> list[str]:
            if field in {"brand", "line", "size", "type", "color"}:
                return [fact.source_id for fact in record.of_kind(field)]
            return [
                source_id
                for source_id, source in record.sources.items()
                if source.field == field
                or source.field == f"tags.{field}"
                or source.field.startswith(f"notes.{field.replace('_', ' ')}:")
                or (
                    field == "fabric_detail"
                    and source.field == "tags.material_detail"
                    and any(
                        fact.kind == "feel" and fact.source_id == source_id for fact in record.facts
                    )
                )
            ]

        first = (
            known_values(anchor, field)
            if field in {"brand", "line", "size", "type", "color"}
            else {
                normalize(anchor.sources[source_id].excerpt)
                for source_id in selected(anchor)
                if anchor.sources[source_id].excerpt is not None
            }
        )
        second = (
            known_values(reference, field)
            if field in {"brand", "line", "size", "type", "color"}
            else {
                normalize(reference.sources[source_id].excerpt)
                for source_id in selected(reference)
                if reference.sources[source_id].excerpt is not None
            }
        )
        status = (
            "no_match"
            if first
            and second
            and (
                any(left != right for left in first for right in second)
                if field in {"brand", "line", "size", "type", "color"}
                else first != second
            )
            else "match"
        )
        supporting = [
            fact
            for record in (anchor, reference)
            for fact in record.facts
            if fact.source_id in selected(record)
        ]
        if status != "no_match" and (
            not first or not second or any(fact.status != "known" for fact in supporting)
        ):
            status = "unresolved"
    return {
        "field": field,
        "status": status,
        **({"display": "can't tell"} if field == "fiber" and status == "unresolved" else {}),
        "candidate_source_ids": sorted(set(selected(anchor))),
        "reference_source_ids": sorted(set(selected(reference))),
    }


def selection(records: list[EvidenceRecord], total: int) -> dict[str, Any]:
    return {
        "eligible_count": total,
        "considered_count": len(records),
        "considered_ids": [record.locator["item_id"] for record in records],
        "limit": 15,
        "truncated": total > len(records),
    }


def rule_rows(
    profile: FitProfile, section: str, anchor: EvidenceRecord
) -> tuple[list[EvidenceRecord], list[dict[str, Any]]]:
    records, results = [], []
    for index, row in enumerate(getattr(profile, section)):
        data = row.model_dump()
        record = parse_row(profile, section, index, data)
        required = {kind: data[kind] for kind in ("brand", "line") if data[kind]}
        results.append(
            {
                "row_key": f"{section}:{index}",
                "status": match_fields(anchor, required),
                "fact_ids": fact_ids(record),
            }
        )
        records.append(record)
    return records, results


async def get_fit_evidence(db: AsyncSession, user_id: UUID, item_id: UUID) -> FitEvidence:
    anchor = await db.scalar(
        select(ClothingItem)
        .join(ItemPurchase, ItemPurchase.item_id == ClothingItem.id)
        .where(
            ClothingItem.id == item_id,
            ClothingItem.user_id == user_id,
            ItemPurchase.state == "considering",
            ClothingItem.status == ItemStatus.ready,
            ClothingItem.is_archived.is_(False),
        )
    )
    if anchor is None:
        raise HTTPException(404, "Considering item not found")
    owned = list(
        await db.scalars(
            select(ClothingItem)
            .where(
                ClothingItem.user_id == user_id,
                owned_item(),
                ClothingItem.status == ItemStatus.ready,
                ClothingItem.is_archived.is_(False),
            )
            .order_by(ClothingItem.id)
        )
    )
    profile = await FitProfileService(db).get(user_id)
    candidate = parse_item(anchor)
    records = [parse_item(item) for item in owned]
    size_rows, sizes = rule_rows(profile, "brand_sizes", candidate)
    skip_rows, skips = rule_rows(profile, "skip_list", candidate)
    recommended = [
        fact
        for row, record in zip(sizes, size_rows, strict=True)
        if row["status"] == "match"
        for fact in record.of_kind("size")
        if fact.status == "known"
    ]
    size_values = {fact.value["normalized"] for fact in recommended}
    size_status = "conflict" if len(size_values) > 1 else "known" if size_values else "unknown"
    if size_status == "conflict":
        for fact in recommended:
            fact.status = "conflict"
            fact.conflict_ids = [
                other.fact_id for other in recommended if other.value != fact.value
            ]
    brand = next(iter(candidate.values("brand")), "") if len(candidate.values("brand")) == 1 else ""
    line = next(iter(candidate.values("line")), "") if len(candidate.values("line")) == 1 else ""
    size_eligible = [
        record
        for record in records
        if brand
        and match_fields(record, {"brand": brand, **({"line": line} if line else {})}) == "match"
    ]
    candidate_fibers = {
        fact.value["name"] for fact in candidate.explicit_fibers() if fact.status == "known"
    }
    fabric_eligible = [
        record
        for record in records
        if candidate_fibers
        & {fact.value["name"] for fact in record.explicit_fibers() if fact.status == "known"}
    ]
    fabric_eligible.sort(
        key=lambda record: (
            -len(
                candidate_fibers
                & {
                    fact.value["name"]
                    for fact in record.explicit_fibers()
                    if fact.status == "known"
                }
            ),
            record.key,
        )
    )
    duplicate_rows = []
    for record in records:
        comparisons = [
            compare(candidate, record, field)
            for field in ("brand", "line", "type", "color", "fiber")
        ]
        statuses = {comparison["status"] for comparison in comparisons}
        status = (
            "no_match"
            if "no_match" in statuses
            else "unresolved"
            if "unresolved" in statuses
            else "match"
        )
        duplicate_rows.append(
            {
                "item_id": record.locator["item_id"],
                "status": status,
                "fact_ids": fact_ids(record),
                "comparisons": comparisons,
            }
        )
    duplicate_rows.sort(
        key=lambda row: (
            {"match": 0, "unresolved": 1, "no_match": 2}[row["status"]],
            -sum(comparison["status"] == "match" for comparison in row["comparisons"]),
            row["item_id"],
        )
    )
    duplicate_ids = {row["item_id"] for row in duplicate_rows[:15]}
    duplicate_refs = [record for record in records if record.locator["item_id"] in duplicate_ids]
    pool, pool_count = outfit_pool(owned)
    outfit_refs = [
        record for record in records if record.locator["item_id"] in {str(item.id) for item in pool}
    ]
    chosen = {
        record.key: record
        for record in [
            candidate,
            *size_rows,
            *skip_rows,
            *size_eligible[:15],
            *fabric_eligible[:15],
            *duplicate_refs,
            *outfit_refs,
        ]
    }
    sources = {
        source_id: source
        for record in chosen.values()
        for source_id, source in record.sources.items()
    }
    facts = [fact for record in chosen.values() for fact in record.facts]
    ideas = outfit_ideas(anchor, pool, pool_count)
    for idea in ideas["ideas"]:
        idea["type_fact_ids"] = [
            fact.fact_id
            for fact in facts
            if fact.kind == "type"
            and sources[fact.source_id].item_id in idea["item_ids"]
            and fact.status == "known"
        ]
    references = [
        {
            "item_id": record.locator["item_id"],
            "shared_fiber_fact_ids": [
                fact.fact_id
                for fact in record.explicit_fibers()
                if fact.status == "known" and fact.value["name"] in candidate_fibers
            ],
            "rating_fact_ids": fact_ids(record, "rating"),
            "feel_fact_ids": fact_ids(record, "feel"),
            "comparisons": [
                compare(candidate, record, field)
                for field in (
                    "brand",
                    "line",
                    "type",
                    "subtype",
                    "fiber",
                    "fit",
                    "style",
                    "fabric_detail",
                    "warmth",
                    "works",
                    "doesnt",
                )
            ],
        }
        for record in fabric_eligible[:15]
    ]
    return FitEvidence(
        anchor_id=str(item_id),
        profile_rev=profile.rev,
        notes_truncated=any(record.notes_truncated for record in chosen.values()),
        sources=sources,
        facts=facts,
        size={
            "status": size_status,
            "rows": sizes,
            "recommended_size_fact_ids": [fact.fact_id for fact in recommended]
            if size_status == "known"
            else [],
            "references": [
                {"item_id": record.locator["item_id"], "fact_ids": fact_ids(record)}
                for record in size_eligible[:15]
            ],
        },
        feel={
            "status": "known"
            if any(
                comparison["status"] in {"match", "no_match"}
                for reference in references
                for comparison in reference["comparisons"]
            )
            else "unresolved"
            if references
            else "unknown",
            "references": references,
            "fiber_notes": quoted_fiber_notes(list(chosen.values())),
        },
        duplicates={
            "status": "match"
            if any(row["status"] == "match" for row in duplicate_rows)
            else "unresolved"
            if any(row["status"] == "unresolved" for row in duplicate_rows)
            else "no_match",
            "matches": [row for row in duplicate_rows[:15] if row["status"] == "match"],
            "unresolved": [row for row in duplicate_rows[:15] if row["status"] == "unresolved"],
        },
        skip={
            "status": "match"
            if any(row["status"] == "match" for row in skips)
            else "unresolved"
            if not skips or any(row["status"] == "unresolved" for row in skips)
            else "no_match",
            "matches": [row for row in skips if row["status"] == "match"],
            "unresolved": [row for row in skips if row["status"] == "unresolved"],
        },
        outfit_ideas_found=ideas,
        selection={
            "size": selection(size_eligible[:15], len(size_eligible)),
            "feel": selection(fabric_eligible[:15], len(fabric_eligible)),
            "duplicates": selection(duplicate_refs, len(records)),
            "outfits": selection(outfit_refs, pool_count),
        },
    )
