import re
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select

from app.models.considering import ItemPurchase, owned_item
from app.models.item import ClothingItem, ItemHistory, ItemStatus
from app.models.learning import UserLearningProfile
from app.services.fit_evidence_parser import normalize, parse_item
from app.services.fit_evidence_service import compare

DIMENSIONS = {
    "fit_and_cut": ("fit",),
    "feel_and_quality": ("fabric_detail", "warmth", "works", "doesnt"),
    "style_and_look": ("style", "color", "works"),
}


def citation(record, source_id):
    return {
        "source_id": source_id,
        **record.sources[source_id].model_dump(),
        "quote": record.sources[source_id].excerpt,
    }


def reason(kind, record, source_ids):
    return {"kind": kind, "citations": [citation(record, source_id) for source_id in source_ids]}


async def build_state(db, user_id, evidence):
    anchor = await db.get(ClothingItem, UUID(evidence.anchor_id))
    purchase = await db.get(ItemPurchase, anchor.id)
    candidate = parse_item(anchor)
    garments = list(
        await db.scalars(
            select(ClothingItem).where(
                ClothingItem.user_id == user_id,
                owned_item(),
                ClothingItem.status == ItemStatus.ready,
                ClothingItem.is_archived.is_(False),
            )
        )
    )
    recent = list(
        await db.execute(
            select(ItemHistory.item_id, ItemHistory.worn_at)
            .join(ClothingItem, ClothingItem.id == ItemHistory.item_id)
            .where(
                ClothingItem.user_id == user_id,
                ItemHistory.worn_at >= date.today() - timedelta(days=90),
            )
        )
    )
    neighbours = []
    for item in garments:
        record = parse_item(item)
        comparisons = [
            compare(candidate, record, field)
            for field in (
                "type",
                "brand",
                "line",
                "fit",
                "style",
                "color",
                "fiber",
                "fabric_detail",
                "warmth",
                "works",
                "doesnt",
            )
        ]
        matches = [row["field"] for row in comparisons if row["status"] == "match"]
        ratings = [
            fact.value["label"] for fact in record.of_kind("rating") if fact.status == "known"
        ]
        notes = {
            source_id: citation(record, source_id)
            for source_id, source in record.sources.items()
            if source.field.startswith("notes.") or source.field == "tags.fit"
        }
        close = "type" in matches and any(field in matches for field in ("brand", "fit", "style"))
        neighbours.append(
            {
                "item_id": str(item.id),
                "name": item.name,
                "rating": ratings[0] if ratings else None,
                "wear_count": item.wear_count,
                "recent_wears": sum(row.item_id == item.id for row in recent),
                "last_worn_at": str(item.last_worn_at) if item.last_worn_at else None,
                "wear_notes": [
                    entry.partition(":")[2].strip()[:300]
                    for entry in re.split(r"[|;\n]", (item.notes or "")[:4096])
                    if normalize(entry.partition(":")[0]) == "wear"
                ],
                "notes": notes,
                "comparisons": comparisons,
                "close": close,
                "record": record,
                "rank": (
                    close,
                    "type" in matches,
                    "fit" in matches,
                    "brand" in matches,
                    "fiber" in matches,
                ),
            }
        )
    neighbours.sort(key=lambda row: (row["rank"], row["wear_count"], row["item_id"]), reverse=True)
    selected = neighbours[:15]
    profile = await db.get(UserLearningProfile, user_id)
    state = {
        "candidate": {
            "item_id": str(anchor.id),
            "name": anchor.name,
            **{
                field: [fact.model_dump() for fact in candidate.of_kind(field)] or None
                for field in ("type", "brand", "line", "color", "fiber", "size")
            },
            **{
                field: [
                    source.excerpt
                    for source in candidate.sources.values()
                    if source.field == f"tags.{field}"
                    or source.field.startswith(f"notes.{field.replace('_', ' ')}:")
                ]
                or None
                for field in ("fit", "fabric_detail", "warmth", "works", "doesnt", "style")
            },
            "planned_size": purchase.planned_size,
            "cut": (anchor.tags or {}).get("fit"),
            "notes": {
                source_id: source.model_dump()
                for source_id, source in candidate.sources.items()
                if source.field.startswith("notes.")
            },
        },
        "similar_items": [
            {key: value for key, value in row.items() if key not in {"record", "rank", "close"}}
            for row in selected
        ],
        "learned": {
            "color_scores": profile.color_scores if profile else None,
            "style_scores": profile.style_scores if profile else None,
            "signal_counts": None,
            "weight": "weaker than direct garment experience",
        },
        "closet": {
            "owned_type_count": sum(item.type == anchor.type for item in garments),
            "owned_color_count": sum(
                item.primary_color == anchor.primary_color for item in garments
            )
            if anchor.primary_color
            else None,
            "recent_wears_of_type": sum(
                row.item_id in {item.id for item in garments if item.type == anchor.type}
                for row in recent
            ),
            "outfits_completed": evidence.outfit_ideas_found,
        },
        "evidence": evidence.model_dump(),
    }
    return state, candidate, selected


def reasons(candidate, neighbours, probabilities):
    pluses, minuses, unknowns = [], [], []
    for dimension, fields in DIMENSIONS.items():
        supported = False
        for neighbour in neighbours:
            if not neighbour["close"] or not neighbour["rating"]:
                continue
            for comparison in neighbour["comparisons"]:
                if comparison["field"] not in fields or comparison["status"] != "match":
                    continue
                record = neighbour["record"]
                source_ids = comparison["reference_source_ids"]
                notes = [
                    source_id
                    for source_id in source_ids
                    if record.sources[source_id].field.startswith("notes.")
                ]
                if not notes:
                    continue
                rating_ids = [
                    fact.source_id for fact in record.of_kind("rating") if fact.status == "known"
                ]
                evidence_reason = reason(dimension, record, list(dict.fromkeys(notes + rating_ids)))
                evidence_reason["citations"] += [
                    citation(candidate, source_id)
                    for source_id in comparison["candidate_source_ids"]
                ]
                probability = probabilities[dimension]
                if probability != 0.5:
                    (pluses if probability > 0.5 else minuses).append(evidence_reason)
                    supported = True
        if not supported:
            source_id = f"{candidate.key}:unknown:{dimension}"
            unknowns.append(
                {
                    "kind": dimension,
                    "citations": [
                        {
                            "source_id": source_id,
                            "item_id": candidate.locator["item_id"],
                            "field": dimension,
                            "quote": None,
                        }
                    ],
                }
            )
    return pluses, minuses, unknowns
