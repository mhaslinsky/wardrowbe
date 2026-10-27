from uuid import uuid4

import pytest

from app.utils.clothing import (
    WardrobeComposition,
    _slots_for_type,
    canonical_item_order,
    count_composition,
    deduplicate_by_body_slot,
    repair_top_layers,
)
from app.utils.garment_vocabulary import (
    DEFAULT_WASH_INTERVALS,
    ITEM_ROLE,
    NEEDS_BASE_TOP_TYPES,
    render_outfit_prompt,
    render_tagging_prompt,
)
from app.utils.prompts import load_prompt


def test_overshirt_is_a_mid_layer_with_three_wears_between_washes():
    assert ITEM_ROLE.get("overshirt") == "mid_layer"
    assert DEFAULT_WASH_INTERVALS.get("overshirt") == 3
    assert "overshirt" in NEEDS_BASE_TOP_TYPES
    assert _slots_for_type("overshirt") == {"mid_layer"}
    assert count_composition([("overshirt", 2)]) == WardrobeComposition(layers=2)


def test_tagger_classifies_shirt_jackets_and_shackets_as_overshirts():
    prompt = render_tagging_prompt(load_prompt("clothing_analysis"))
    assert (
        "Classify shirt jackets, utility overshirts and shackets as overshirt, not shirt or jacket."
        in prompt
    )


def test_recommendation_prompt_names_overshirt_as_a_middle_layer():
    prompt = render_outfit_prompt(load_prompt("recommendation"))
    assert "a vest, cardigan, overshirt" in prompt
    assert "An overshirt (type overshirt" in prompt


@pytest.mark.parametrize("subtype", [None, "", "utility", "shirt-jacket", "unexpected"])
def test_overshirt_type_gets_a_thin_base_top_regardless_of_subtype(subtype):
    layer, tee, pants, polo = [uuid4() for _ in range(4)]
    types = {layer: "overshirt", tee: "t-shirt", pants: "pants", polo: "polo"}
    subtypes = {} if subtype is None else {layer: subtype}
    repaired = repair_top_layers([layer, pants, polo], [polo, tee, layer], types, subtypes)
    assert repaired == [layer, pants, tee]
    assert repair_top_layers(repaired, [polo, tee, layer], types, subtypes) == repaired
    assert canonical_item_order(repaired, types) == [tee, layer, pants]


def test_overshirt_does_not_claim_base_top_or_outerwear_slots():
    layer, tee, pants, coat = [uuid4() for _ in range(4)]
    types = {layer: "overshirt", tee: "t-shirt", pants: "pants", coat: "coat"}
    selected = [layer, tee, pants, coat]
    assert deduplicate_by_body_slot(selected, types) == selected
    assert repair_top_layers(selected, selected, types, {}) == selected
    assert repair_top_layers([], selected, types, {}) == []


def test_unpinned_overshirt_is_removed_when_no_thin_base_exists():
    layer, polo, pants = [uuid4() for _ in range(3)]
    types = {layer: "overshirt", polo: "polo", pants: "pants"}
    assert repair_top_layers([layer, pants], [polo], types, {}) == [pants, polo]


def test_pinned_overshirt_keeps_its_base_within_three_upper_layers():
    layer, hoodie, coat, tee = [uuid4() for _ in range(4)]
    types = {layer: "overshirt", hoodie: "hoodie", coat: "coat", tee: "t-shirt"}
    assert repair_top_layers([layer, hoodie, coat], [tee], types, {}, {layer, hoodie}) == [
        layer,
        hoodie,
        tee,
    ]
