import logging
from dataclasses import dataclass
from uuid import UUID

from app.utils.garment_vocabulary import CAN_BE_MID_LAYER_TYPES, ITEM_ROLE, ROLES

logger = logging.getLogger(__name__)

# A suit takes its own slot rather than outer_layer so an overcoat can still go over it.
ROLE_SLOTS: dict[str, frozenset[str]] = {
    "full_body": frozenset({"base_top", "bottom"}),
    "suit": frozenset({"bottom", "suit"}),
}

# The slots an outfit must cover; a suit's own slot is optional like an outer layer.
BODY_SLOTS = frozenset({"base_top", "bottom"})

# Shirts cut to be worn open over another top. They stay type shirt so they wash and score like
# shirts, but one of them is never the outfit's only top.
OVER_LAYER_SUBTYPES = frozenset({"overshirt", "chore-jacket", "shirt-jacket", "shacket"})


@dataclass(frozen=True)
class WardrobeComposition:
    base_tops: int = 0
    layers: int = 0
    bottoms: int = 0
    full_body: int = 0


def count_composition(type_counts: list[tuple[str | None, int]]) -> WardrobeComposition:
    """Bucket item counts by body role. Outer layers, footwear and accessories are ignored."""
    buckets = {"base_top": 0, "mid_layer": 0, "bottom": 0, "full_body": 0}
    for item_type, count in type_counts:
        role = ITEM_ROLE.get((item_type or "").lower())
        if role in buckets:
            buckets[role] += count
    return WardrobeComposition(
        base_tops=buckets["base_top"],
        layers=buckets["mid_layer"],
        bottoms=buckets["bottom"],
        full_body=buckets["full_body"],
    )


def _slots_for_type(item_type: str) -> frozenset[str]:
    role = ITEM_ROLE.get(item_type)
    if not role or role == "accessory":
        return frozenset()
    return ROLE_SLOTS.get(role, frozenset({role}))


def covered_body_slots(item_ids: list[UUID], item_type_map: dict[UUID, str]) -> set[str]:
    return {
        slot
        for iid in item_ids
        for slot in _slots_for_type(item_type_map.get(iid, ""))
        if slot in BODY_SLOTS
    }


def lacks_top_under_overshirt(
    item_ids: list[UUID], item_type_map: dict[UUID, str], item_subtype_map: dict[UUID, str]
) -> bool:
    """True when the outfit has an overshirt and nothing else covering the top: no shirt, tee or
    sweater under it, and no dress or jumpsuit."""
    overshirts = {
        iid for iid in item_ids if (item_subtype_map.get(iid) or "").lower() in OVER_LAYER_SUBTYPES
    }
    if not overshirts:
        return False
    others = [iid for iid in item_ids if iid not in overshirts]
    return "base_top" not in covered_body_slots(others, item_type_map)


def top_to_wear_under(
    ranked_ids: list[UUID],
    chosen: set[UUID],
    item_type_map: dict[UUID, str],
    item_subtype_map: dict[UUID, str],
) -> UUID | None:
    """The best-ranked candidate that works as a top on its own, to put under an overshirt."""
    for iid in ranked_ids:
        if iid in chosen or (item_subtype_map.get(iid) or "").lower() in OVER_LAYER_SUBTYPES:
            continue
        if ITEM_ROLE.get(item_type_map.get(iid, "")) == "base_top":
            return iid
    return None


def _seat_in_mid_layer(
    iid: UUID, slot: str, item_type_map: dict[UUID, str], claimed: dict[str, UUID]
) -> bool:
    """Keep a second top or outer layer by putting it, or the piece already holding its slot,
    in the free mid layer. A dress or suit holding the slot is never layered over."""
    if slot not in ("base_top", "outer_layer") or "mid_layer" in claimed:
        return False
    occupant = claimed[slot]
    if _slots_for_type(item_type_map.get(occupant, "")) != {slot}:
        return False
    if item_type_map.get(iid, "") in CAN_BE_MID_LAYER_TYPES:
        claimed["mid_layer"] = iid
        return True
    if item_type_map.get(occupant, "") in CAN_BE_MID_LAYER_TYPES:
        claimed["mid_layer"] = occupant
        claimed[slot] = iid
        return True
    return False


def deduplicate_by_body_slot(
    item_ids: list[UUID],
    item_type_map: dict[UUID, str],
    mandatory_item_ids: set[UUID] | None = None,
) -> list[UUID]:
    requested = mandatory_item_ids or set()

    # A mandatory item only claims its slots if the caller actually passed it in item_ids;
    # one that never made the candidate list must not block the items that did.
    # Mandatory items compete with each other too, first in list order wins, because two
    # shirts or a dress plus trousers is an unwearable outfit however it was requested.
    # Multi-slot items (dress, suit) then claim before single-slot ones, so a dress wins
    # over separates wherever it appears in the list.
    mandatory = [iid for iid in item_ids if iid in requested]
    rest = [iid for iid in item_ids if iid not in requested]
    multi_slot = [iid for iid in rest if len(_slots_for_type(item_type_map.get(iid, ""))) > 1]
    multi_slot_ids = set(multi_slot)
    single_slot = [iid for iid in rest if iid not in multi_slot_ids]

    claimed: dict[str, UUID] = {}
    kept: set[UUID] = set()
    for iid in mandatory + multi_slot + single_slot:
        item_type = item_type_map.get(iid, "")
        slots = _slots_for_type(item_type)
        taken = next((slot for slot in sorted(slots) if slot in claimed), None)
        if taken and len(slots) == 1 and _seat_in_mid_layer(iid, taken, item_type_map, claimed):
            kept.add(iid)
            continue
        if taken:
            logger.warning(
                f"Removing {item_type} item {iid}: {taken} already filled by {claimed[taken]}"
            )
            continue
        for slot in slots:
            claimed[slot] = iid
        kept.add(iid)
    return [iid for iid in item_ids if iid in kept]


_ROLE_SORT_INDEX: dict[str, int] = {role: idx for idx, role in enumerate(ROLES)}


def remove_legwear(
    item_ids: list[UUID], item_type_map: dict[UUID, str], keep: set[UUID]
) -> list[UUID]:
    kept = []
    for iid in item_ids:
        item_type = item_type_map.get(iid, "")
        if iid not in keep and ITEM_ROLE.get(item_type) == "legwear":
            logger.info(f"Removing {item_type} item {iid}: too warm for legwear")
            continue
        kept.append(iid)
    return kept


def canonical_item_order(item_ids: list[UUID], item_type_map: dict[UUID, str]) -> list[UUID]:
    original_positions = {iid: idx for idx, iid in enumerate(item_ids)}

    def sort_key(item_id: UUID) -> tuple[int, int]:
        item_type = item_type_map.get(item_id, "")
        role = ITEM_ROLE.get(item_type)
        role_idx = _ROLE_SORT_INDEX.get(role, len(ROLES)) if role else len(ROLES)
        return (role_idx, original_positions[item_id])

    return sorted(item_ids, key=sort_key)
