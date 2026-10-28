from itertools import combinations
from typing import Any
from uuid import UUID

from app.models.item import ClothingItem, ItemStatus
from app.services.fit_evidence_parser import normalize
from app.utils.clothing import (
    BODY_SLOTS,
    OVER_LAYER_SUBTYPES,
    canonical_item_order,
    covered_body_slots,
    deduplicate_by_body_slot,
)
from app.utils.garment_vocabulary import (
    BASE_UNDER_LAYER_TYPES,
    ITEM_ROLE,
    NEEDS_BASE_TOP_TYPES,
    THIN_BASE_SUBTYPES,
    THIN_BASE_TOP_TYPES,
)


def valid_outfit(item_ids: list[UUID], anchor_id: UUID, items: dict[UUID, ClothingItem]) -> bool:
    if (
        not 2 <= len(item_ids) <= 20
        or len(set(item_ids)) != len(item_ids)
        or item_ids.count(anchor_id) != 1
    ):
        return False
    if any(
        item_id not in items
        or normalize(items[item_id].type) not in ITEM_ROLE
        or ITEM_ROLE[normalize(items[item_id].type)] == "underwear"
        for item_id in item_ids
    ):
        return False
    if any(
        items[item_id].user_id != items[anchor_id].user_id
        or items[item_id].status != ItemStatus.ready
        or items[item_id].is_archived
        for item_id in item_ids
    ):
        return False
    types = {item_id: normalize(items[item_id].type) for item_id in item_ids}
    roles = [ITEM_ROLE[types[item_id]] for item_id in item_ids]
    if roles.count("footwear") != 1 or not BODY_SLOTS <= covered_body_slots(item_ids, types):
        return False
    if (
        sum(role in {"base_top", "mid_layer", "outer_layer", "full_body", "suit"} for role in roles)
        > 3
    ):
        return False
    overshirts = {
        item_id
        for item_id in item_ids
        if types[item_id] == "overshirt"
        or (items[item_id].subtype or "").lower() in OVER_LAYER_SUBTYPES
    }
    bases = [
        item_id
        for item_id in item_ids
        if item_id not in overshirts and types[item_id] not in NEEDS_BASE_TOP_TYPES
    ]
    if overshirts and (
        "full_body" in roles
        or not any(
            types[item_id] in THIN_BASE_TOP_TYPES
            or (items[item_id].subtype or "").lower() in THIN_BASE_SUBTYPES.get(types[item_id], ())
            for item_id in bases
        )
    ):
        return False
    if any(types[item_id] in NEEDS_BASE_TOP_TYPES for item_id in item_ids) and not any(
        types[item_id] in BASE_UNDER_LAYER_TYPES or ITEM_ROLE[types[item_id]] == "full_body"
        for item_id in bases
    ):
        return False
    return deduplicate_by_body_slot(item_ids, types, {anchor_id}) == item_ids


def outfit_pool(owned: list[ClothingItem]) -> tuple[list[ClothingItem], int]:
    eligible = [
        item
        for item in owned
        if normalize(item.type) in ITEM_ROLE and ITEM_ROLE[normalize(item.type)] != "underwear"
    ]
    selected = []
    for role in ("base_top", "bottom", "full_body", "footwear"):
        first = next((item for item in eligible if ITEM_ROLE[normalize(item.type)] == role), None)
        if first is not None:
            selected.append(first)
    thin = next(
        (
            item
            for item in eligible
            if normalize(item.type) in THIN_BASE_TOP_TYPES
            or (item.subtype or "").lower() in THIN_BASE_SUBTYPES.get(normalize(item.type), ())
        ),
        None,
    )
    if thin is not None and thin not in selected:
        selected.append(thin)
    return (selected + [item for item in eligible if item not in selected])[:15], len(eligible)


def outfit_ideas(
    anchor: ClothingItem, pool: list[ClothingItem], eligible_count: int
) -> dict[str, Any]:
    result = {
        "status": "known",
        "count": 0,
        "ideas": [],
        "eligible_count": eligible_count,
        "considered_ids": [str(item.id) for item in pool],
        "limit": 12,
        "truncated": eligible_count > len(pool),
        "rejected_count": 0,
        "scope": "sampled closet composition, 2-5 garments; ignores laundry and weather",
    }
    if normalize(anchor.type) not in ITEM_ROLE:
        return {**result, "status": "unknown"}
    if ITEM_ROLE[normalize(anchor.type)] == "underwear":
        return {**result, "status": "not_applicable"}
    items = {item.id: item for item in [anchor, *pool]}
    types = {item.id: normalize(item.type) for item in items.values()}
    for size in range(1, min(4, len(pool)) + 1):
        for companions in combinations(pool, size):
            item_ids = [anchor.id, *(item.id for item in companions)]
            if not valid_outfit(item_ids, anchor.id, items):
                result["rejected_count"] += 1
                continue
            if result["count"] == 12:
                result["truncated"] = True
                return result
            result["ideas"].append(
                {"item_ids": [str(item_id) for item_id in canonical_item_order(item_ids, types)]}
            )
            result["count"] += 1
    return result
