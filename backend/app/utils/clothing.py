import logging
from dataclasses import dataclass
from uuid import UUID

from app.utils.garment_vocabulary import (
    BASE_UNDER_LAYER_TYPES,
    CAN_BE_MID_LAYER_TYPES,
    ITEM_ROLE,
    NEEDS_BASE_TOP_TYPES,
    ROLES,
    THIN_BASE_SUBTYPES,
    THIN_BASE_TOP_TYPES,
)

logger = logging.getLogger(__name__)

# A suit takes its own slot rather than outer_layer so an overcoat can still go over it.
ROLE_SLOTS: dict[str, frozenset[str]] = {
    "full_body": frozenset({"base_top", "bottom"}),
    "suit": frozenset({"bottom", "suit"}),
}

# The slots an outfit must cover; a suit's own slot is optional like an outer layer.
BODY_SLOTS = frozenset({"base_top", "bottom"})

# Legacy shirt subtypes still need a thin base until their items are retyped as overshirts.
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
    if not role or role in {"accessory", "underwear"}:
        return frozenset()
    return ROLE_SLOTS.get(role, frozenset({role}))


def covered_body_slots(item_ids: list[UUID], item_type_map: dict[UUID, str]) -> set[str]:
    return {
        slot
        for iid in item_ids
        for slot in _slots_for_type(item_type_map.get(iid, ""))
        if slot in BODY_SLOTS
    }


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


def _item_role(item_id: UUID, item_type_map: dict[UUID, str]) -> str | None:
    return ITEM_ROLE.get(item_type_map.get(item_id, ""))


def prefer_ranked_layers(
    item_ids: list[UUID],
    ranked_ids: list[UUID],
    item_type_map: dict[UUID, str],
    mandatory_item_ids: set[UUID] | None = None,
) -> list[UUID]:
    """Keep requested sweater-type layers, or the best-ranked selected one."""
    mandatory = mandatory_item_ids or set()
    layers = {item_id for item_id in item_ids if item_type_map.get(item_id) in NEEDS_BASE_TOP_TYPES}
    if len(layers) < 2:
        return list(item_ids)
    pinned = layers & mandatory
    best = next((item_id for item_id in ranked_ids if item_id in layers), None)
    if best is None:
        best = next(item_id for item_id in item_ids if item_id in layers)
    keep_layers = pinned or {best}
    return [item_id for item_id in item_ids if item_id not in layers or item_id in keep_layers]


@dataclass
class _LayerRepair:
    item_ids: list[UUID]
    ranked_ids: list[UUID]
    item_type_map: dict[UUID, str]
    item_subtype_map: dict[UUID, str]
    mandatory: set[UUID]

    def is_overshirt(self, item_id: UUID) -> bool:
        return (
            self.item_type_map.get(item_id) == "overshirt"
            or (self.item_subtype_map.get(item_id) or "").lower() in OVER_LAYER_SUBTYPES
        )

    def is_base(self, item_id: UUID, thin: bool = False) -> bool:
        kind = self.item_type_map.get(item_id, "")
        if self.is_overshirt(item_id):
            return False
        if thin:
            return kind in THIN_BASE_TOP_TYPES or (
                (self.item_subtype_map.get(item_id) or "").lower()
                in THIN_BASE_SUBTYPES.get(kind, frozenset())
            )
        return kind in BASE_UNDER_LAYER_TYPES

    def needs_base(self, item_id: UUID) -> bool:
        return self.item_type_map.get(item_id) in NEEDS_BASE_TOP_TYPES

    def is_under_layer(self, item_id: UUID) -> bool:
        return self.needs_base(item_id) or bool(
            _slots_for_type(self.item_type_map.get(item_id, "")) & {"base_top", "mid_layer"}
        )

    def best_base(self, thin: bool = False) -> UUID | None:
        return next((item_id for item_id in self.ranked_ids if self.is_base(item_id, thin)), None)

    def can_add_base(self) -> bool:
        pinned_upper = [
            item_id
            for item_id in self.item_ids
            if item_id in self.mandatory
            and (
                self.is_under_layer(item_id)
                or _item_role(item_id, self.item_type_map) == "outer_layer"
            )
        ]
        if len(pinned_upper) >= 3:
            logger.warning("Pinned layers leave no room for a base top")
            return False
        return True

    def fit_overshirt(self) -> None:
        overshirts = {item_id for item_id in self.item_ids if self.is_overshirt(item_id)}
        if not overshirts:
            return
        incompatible_pins = {
            item_id
            for item_id in self.item_ids
            if item_id in self.mandatory
            and item_id not in overshirts
            and self.is_under_layer(item_id)
            and not self.is_base(item_id, True)
        }
        if incompatible_pins:
            logger.warning("Pinned tops conflict with overshirt layering")
            self.item_ids = [
                item_id
                for item_id in self.item_ids
                if item_id not in overshirts or item_id in self.mandatory
            ]
            overshirts.intersection_update(self.item_ids)
        if not overshirts:
            return
        under = next((item_id for item_id in self.item_ids if self.is_base(item_id, True)), None)
        if under is None:
            under = self.best_base(thin=True)
        if under is None:
            others_cover_top = any(
                self.is_under_layer(item_id) and item_id not in overshirts
                for item_id in self.item_ids
            )
            fallback = top_to_wear_under(
                self.ranked_ids, set(self.item_ids), self.item_type_map, self.item_subtype_map
            )
            if not others_cover_top and fallback is None:
                logger.warning("No other top available; keeping the overshirt")
                return
            logger.warning("No thin top available; keeping pinned overshirts only")
            self.item_ids = [
                item_id
                for item_id in self.item_ids
                if item_id not in overshirts or item_id in self.mandatory
            ]
            return
        if under not in self.item_ids and not self.can_add_base():
            return
        self.item_ids = [
            item_id
            for item_id in self.item_ids
            if item_id in overshirts
            or item_id == under
            or item_id in self.mandatory
            or not self.is_under_layer(item_id)
        ]
        if under not in self.item_ids:
            self.item_ids.append(under)

    def ensure_base_under_layer(self) -> None:
        if not any(self.needs_base(item_id) for item_id in self.item_ids):
            return
        covering = [
            item_id
            for item_id in self.item_ids
            if not self.needs_base(item_id) and not self.is_overshirt(item_id)
        ]
        if "base_top" in covered_body_slots(covering, self.item_type_map) and any(
            self.is_base(item_id) or _item_role(item_id, self.item_type_map) == "full_body"
            for item_id in covering
        ):
            return
        if not self.can_add_base():
            return
        _, slots = _assign_body_slots(self.item_ids, self.item_type_map, self.mandatory)
        current_base = slots.get("base_top")
        if (
            current_base in self.mandatory
            and not self.needs_base(current_base)
            and not self.is_overshirt(current_base)
        ):
            logger.warning("Pinned base top conflicts with sweater layering")
            return
        under = self.best_base()
        if under is None:
            logger.warning("No base top available under the sweater or hoodie")
            return
        if (
            current_base is not None
            and not self.needs_base(current_base)
            and not self.is_overshirt(current_base)
        ):
            self.item_ids.remove(current_base)
        if under not in self.item_ids:
            self.item_ids.append(under)

    def ensure_top(self) -> None:
        if any(self.is_under_layer(item_id) for item_id in self.item_ids):
            return
        if not any(_item_role(item_id, self.item_type_map) for item_id in self.item_ids):
            return
        fallback = top_to_wear_under(
            self.ranked_ids, set(self.item_ids), self.item_type_map, self.item_subtype_map
        )
        if fallback is None:
            self.item_ids = []
        else:
            self.item_ids.append(fallback)

    def cap_upper_layers(self) -> None:
        upper = [
            item_id
            for item_id in self.item_ids
            if self.is_under_layer(item_id)
            or _item_role(item_id, self.item_type_map) == "outer_layer"
        ]
        protected = self.mandatory | {
            item_id
            for item_id in self.item_ids
            if self.is_base(item_id) or _item_role(item_id, self.item_type_map) == "full_body"
        }
        removable = sorted(
            (item_id for item_id in reversed(upper) if item_id not in protected),
            key=lambda item_id: (
                _item_role(item_id, self.item_type_map) == "outer_layer"
                and not self.needs_base(item_id)
            ),
        )
        for item_id in removable:
            if len(upper) <= 3:
                break
            self.item_ids.remove(item_id)
            upper.remove(item_id)
        if len(upper) > 3:
            logger.warning("Pinned items exceed three upper-body layers")


def repair_top_layers(
    item_ids: list[UUID],
    ranked_ids: list[UUID],
    item_type_map: dict[UUID, str],
    item_subtype_map: dict[UUID, str],
    mandatory_item_ids: set[UUID] | None = None,
) -> list[UUID]:
    """Add missing base tops and keep overshirts over thin tops only."""
    mandatory = mandatory_item_ids or set()
    selected = prefer_ranked_layers(item_ids, ranked_ids, item_type_map, mandatory)
    kept = deduplicate_by_body_slot(selected, item_type_map, mandatory)
    repair = _LayerRepair(kept, ranked_ids, item_type_map, item_subtype_map, mandatory & set(kept))
    had_top = any(repair.is_under_layer(item_id) for item_id in item_ids)
    repair.fit_overshirt()
    if had_top:
        repair.ensure_top()
    repair.ensure_base_under_layer()
    repair.cap_upper_layers()
    return repair.item_ids


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


def _assign_body_slots(
    item_ids: list[UUID],
    item_type_map: dict[UUID, str],
    mandatory_item_ids: set[UUID] | None = None,
) -> tuple[list[UUID], dict[str, UUID]]:
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
    return [iid for iid in item_ids if iid in kept], claimed


def deduplicate_by_body_slot(
    item_ids: list[UUID],
    item_type_map: dict[UUID, str],
    mandatory_item_ids: set[UUID] | None = None,
) -> list[UUID]:
    kept, _ = _assign_body_slots(item_ids, item_type_map, mandatory_item_ids)
    return kept


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
