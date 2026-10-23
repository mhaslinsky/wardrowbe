from itertools import combinations
from uuid import uuid4

import pytest

from app.utils.clothing import (
    ITEM_ROLE,
    WardrobeComposition,
    canonical_item_order,
    count_composition,
    deduplicate_by_body_slot,
    lacks_top_under_overshirt,
    remove_legwear,
    repair_top_layers,
    top_to_wear_under,
)
from app.utils.garment_vocabulary import ROLES


def _ids(n):
    return [uuid4() for _ in range(n)]


def test_removes_duplicate_bottom():
    pants_id, shorts_id, shirt_id, shoes_id = _ids(4)
    item_type_map = {
        shirt_id: "shirt",
        pants_id: "pants",
        shorts_id: "shorts",
        shoes_id: "sneakers",
    }
    result = deduplicate_by_body_slot([shirt_id, pants_id, shorts_id, shoes_id], item_type_map)
    assert pants_id in result
    assert shorts_id not in result
    assert len(result) == 3


def test_removes_duplicate_base_top():
    tshirt_id, polo_id, pants_id = _ids(3)
    item_type_map = {
        tshirt_id: "t-shirt",
        polo_id: "polo",
        pants_id: "jeans",
    }
    result = deduplicate_by_body_slot([tshirt_id, polo_id, pants_id], item_type_map)
    assert tshirt_id in result
    assert polo_id not in result
    assert pants_id in result


def test_allows_layering_base_top_plus_mid_layer():
    tshirt_id, cardigan_id, pants_id = _ids(3)
    item_type_map = {
        tshirt_id: "t-shirt",
        cardigan_id: "cardigan",
        pants_id: "jeans",
    }
    result = deduplicate_by_body_slot([tshirt_id, cardigan_id, pants_id], item_type_map)
    assert tshirt_id in result
    assert cardigan_id in result
    assert pants_id in result
    assert len(result) == 3


def test_allows_layering_base_top_plus_outer_layer():
    shirt_id, jacket_id, pants_id = _ids(3)
    item_type_map = {
        shirt_id: "shirt",
        jacket_id: "jacket",
        pants_id: "pants",
    }
    result = deduplicate_by_body_slot([shirt_id, jacket_id, pants_id], item_type_map)
    assert len(result) == 3


@pytest.mark.parametrize(
    ("first", "second"),
    [("t-shirt", "shirt"), ("shirt", "t-shirt"), ("shirt", "sweater"), ("sweater", "polo")],
)
def test_layerable_top_over_another_top_is_kept_in_either_order(first, second):
    under, over, pants = _ids(3)
    item_type_map = {under: first, over: second, pants: "jeans"}
    assert deduplicate_by_body_slot([under, over, pants], item_type_map) == [under, over, pants]


@pytest.mark.parametrize("outer", ["jacket", "coat"])
def test_hoodie_under_an_outer_layer_is_kept_in_either_order(outer):
    tee, hoodie, coat = _ids(3)
    item_type_map = {tee: "t-shirt", hoodie: "hoodie", coat: outer}
    assert deduplicate_by_body_slot([tee, hoodie, coat], item_type_map) == [tee, hoodie, coat]
    assert deduplicate_by_body_slot([tee, coat, hoodie], item_type_map) == [tee, coat, hoodie]


def test_tee_overshirt_and_jacket_make_three_layers():
    tee, shirt, jacket, pants = _ids(4)
    item_type_map = {tee: "t-shirt", shirt: "shirt", jacket: "jacket", pants: "pants"}
    ids = [tee, shirt, jacket, pants]
    assert deduplicate_by_body_slot(ids, item_type_map) == ids


def test_fourth_upper_layer_is_dropped_once_the_mid_layer_is_taken():
    shirt, sweater, hoodie, jacket = _ids(4)
    item_type_map = {shirt: "shirt", sweater: "sweater", hoodie: "hoodie", jacket: "jacket"}
    assert deduplicate_by_body_slot([shirt, sweater, hoodie, jacket], item_type_map) == [
        shirt,
        sweater,
        hoodie,
    ]


def test_cardigan_takes_the_mid_layer_so_a_second_top_is_dropped():
    tee, cardigan, shirt = _ids(3)
    item_type_map = {tee: "t-shirt", cardigan: "cardigan", shirt: "shirt"}
    assert deduplicate_by_body_slot([tee, cardigan, shirt], item_type_map) == [tee, cardigan]


def test_two_coats_still_keep_only_the_first():
    coat, jacket = _ids(2)
    item_type_map = {coat: "coat", jacket: "jacket"}
    assert deduplicate_by_body_slot([coat, jacket], item_type_map) == [coat]


def test_layerable_top_is_not_added_over_a_dress():
    dress, shirt = _ids(2)
    item_type_map = {dress: "dress", shirt: "shirt"}
    assert deduplicate_by_body_slot([dress, shirt], item_type_map) == [dress]


def test_keeps_first_item_per_slot():
    jeans_id, skirt_id = _ids(2)
    item_type_map = {jeans_id: "jeans", skirt_id: "skirt"}
    result = deduplicate_by_body_slot([jeans_id, skirt_id], item_type_map)
    assert result == [jeans_id]


def test_full_body_removes_separate_top_and_bottom():
    dress_id, shirt_id, pants_id, shoes_id = _ids(4)
    item_type_map = {
        dress_id: "dress",
        shirt_id: "shirt",
        pants_id: "pants",
        shoes_id: "shoes",
    }
    result = deduplicate_by_body_slot([dress_id, shirt_id, pants_id, shoes_id], item_type_map)
    assert dress_id in result
    assert shoes_id in result
    assert shirt_id not in result
    assert pants_id not in result


def test_preserves_unknown_types():
    unknown_id, shirt_id, pants_id = _ids(3)
    item_type_map = {
        unknown_id: "something-new",
        shirt_id: "shirt",
        pants_id: "pants",
    }
    result = deduplicate_by_body_slot([unknown_id, shirt_id, pants_id], item_type_map)
    assert unknown_id in result
    assert shirt_id in result
    assert pants_id in result


def test_no_duplicates_passes_through():
    shirt_id, pants_id, shoes_id, jacket_id = _ids(4)
    item_type_map = {
        shirt_id: "shirt",
        pants_id: "pants",
        shoes_id: "sneakers",
        jacket_id: "jacket",
    }
    ids = [shirt_id, pants_id, shoes_id, jacket_id]
    result = deduplicate_by_body_slot(ids, item_type_map)
    assert result == ids


def test_socks_get_own_slot():
    socks_id, shoes_id, shirt_id = _ids(3)
    item_type_map = {socks_id: "socks", shoes_id: "sneakers", shirt_id: "shirt"}
    result = deduplicate_by_body_slot([socks_id, shoes_id, shirt_id], item_type_map)
    assert socks_id in result
    assert shoes_id in result
    assert len(result) == 3


def test_tights_coexist_with_skirt_and_socks_but_only_one_legwear_item():
    skirt, tights, other_tights, socks, shoes = _ids(5)
    item_type_map = {
        skirt: "skirt",
        tights: "tights",
        other_tights: "tights",
        socks: "socks",
        shoes: "shoes",
    }
    result = deduplicate_by_body_slot([skirt, tights, other_tights, socks, shoes], item_type_map)
    assert result == [skirt, tights, socks, shoes]


def test_tights_coexist_with_dress():
    dress, tights, shoes = _ids(3)
    item_type_map = {dress: "dress", tights: "tights", shoes: "shoes"}
    assert deduplicate_by_body_slot([dress, tights, shoes], item_type_map) == [dress, tights, shoes]


def test_multiple_accessories_allowed():
    hat_id, scarf_id, belt_id, shirt_id = _ids(4)
    item_type_map = {
        hat_id: "hat",
        scarf_id: "scarf",
        belt_id: "belt",
        shirt_id: "shirt",
    }
    result = deduplicate_by_body_slot([hat_id, scarf_id, belt_id, shirt_id], item_type_map)
    assert len(result) == 4


def test_suit_replaces_trousers_but_keeps_shirt_and_overcoat():
    suit, other_suit, shirt, pants, coat, tie, shoes = _ids(7)
    item_type_map = {
        suit: "suit",
        other_suit: "suit",
        shirt: "shirt",
        pants: "pants",
        coat: "coat",
        tie: "tie",
        shoes: "shoes",
    }
    result = deduplicate_by_body_slot(
        [shirt, pants, suit, other_suit, coat, tie, shoes], item_type_map
    )
    assert result == [shirt, suit, coat, tie, shoes]


def test_suit_allows_a_mid_layer_under_it():
    suit, shirt, vest = _ids(3)
    item_type_map = {suit: "suit", shirt: "shirt", vest: "vest"}
    assert deduplicate_by_body_slot([shirt, vest, suit], item_type_map) == [shirt, vest, suit]


def test_first_multi_slot_item_wins_when_they_overlap():
    dress, suit = _ids(2)
    item_type_map = {dress: "dress", suit: "suit"}
    assert deduplicate_by_body_slot([dress, suit], item_type_map) == [dress]
    assert deduplicate_by_body_slot([suit, dress], item_type_map) == [suit]


def test_two_dresses_keep_only_the_first():
    first, second = _ids(2)
    item_type_map = {first: "dress", second: "jumpsuit"}
    assert deduplicate_by_body_slot([first, second], item_type_map) == [first]


def test_mandatory_suit_beats_later_trousers_and_keeps_shirt():
    suit, shirt, pants = _ids(3)
    item_type_map = {suit: "suit", shirt: "shirt", pants: "pants"}
    result = deduplicate_by_body_slot(
        [pants, shirt, suit], item_type_map, mandatory_item_ids={suit}
    )
    assert result == [shirt, suit]


def test_mandatory_trousers_beat_a_suit():
    suit, pants = _ids(2)
    item_type_map = {suit: "suit", pants: "pants"}
    result = deduplicate_by_body_slot([suit, pants], item_type_map, mandatory_item_ids={pants})
    assert result == [pants]


def test_canonical_order_puts_suit_with_the_outer_layers():
    shirt, suit, shoes = _ids(3)
    item_type_map = {shirt: "shirt", suit: "suit", shoes: "shoes"}
    assert canonical_item_order([shoes, suit, shirt], item_type_map) == [shirt, suit, shoes]


def test_canonical_order_covers_every_item_role():
    assert set(ITEM_ROLE.values()) <= set(ROLES)


def test_canonical_role_order_is_the_vocabulary_role_order():
    assert ROLES == (
        "full_body",
        "base_top",
        "mid_layer",
        "suit",
        "outer_layer",
        "bottom",
        "legwear",
        "footwear",
        "socks",
        "neckwear",
        "accessory",
    )


def test_canonical_item_order_sorts_by_role():
    dress_id, shoes_id, hat_id = _ids(3)
    item_type_map = {
        hat_id: "hat",
        shoes_id: "sneakers",
        dress_id: "dress",
    }
    result = canonical_item_order([hat_id, shoes_id, dress_id], item_type_map)
    assert result[0] == dress_id
    assert result[1] == shoes_id
    assert result[2] == hat_id


def test_canonical_order_preserves_position_within_same_role():
    hat1, hat2, shirt_id = _ids(3)
    item_type_map = {hat1: "hat", hat2: "scarf", shirt_id: "shirt"}
    result = canonical_item_order([hat1, hat2, shirt_id], item_type_map)
    assert result[0] == shirt_id
    hat_indices = [i for i, x in enumerate(result) if x in (hat1, hat2)]
    assert hat_indices == [1, 2]


def test_canonical_order_full_outfit():
    shirt_id, pants_id, shoes_id, jacket_id, socks_id = _ids(5)
    item_type_map = {
        socks_id: "socks",
        shoes_id: "sneakers",
        jacket_id: "jacket",
        pants_id: "jeans",
        shirt_id: "t-shirt",
    }
    result = canonical_item_order(
        [socks_id, shoes_id, jacket_id, pants_id, shirt_id], item_type_map
    )
    role_order = [ITEM_ROLE.get(item_type_map[iid], "") for iid in result]
    expected_roles = ["base_top", "outer_layer", "bottom", "footwear", "socks"]
    assert role_order == expected_roles


def test_canonical_order_empty_list():
    result = canonical_item_order([], {})
    assert result == []


def test_mandatory_item_overrides_earlier_duplicate():
    ai_shirt_id, mandatory_shirt_id, pants_id = _ids(3)
    item_type_map = {
        ai_shirt_id: "t-shirt",
        mandatory_shirt_id: "polo",
        pants_id: "jeans",
    }
    result = deduplicate_by_body_slot(
        [ai_shirt_id, mandatory_shirt_id, pants_id],
        item_type_map,
        mandatory_item_ids={mandatory_shirt_id},
    )
    assert mandatory_shirt_id in result
    assert ai_shirt_id not in result
    assert pants_id in result


def test_mandatory_separate_drops_non_mandatory_full_body():
    dress_id, mandatory_pants_id, shoes_id = _ids(3)
    item_type_map = {
        dress_id: "dress",
        mandatory_pants_id: "pants",
        shoes_id: "sneakers",
    }
    result = deduplicate_by_body_slot(
        [dress_id, mandatory_pants_id, shoes_id],
        item_type_map,
        mandatory_item_ids={mandatory_pants_id},
    )
    assert mandatory_pants_id in result
    assert dress_id not in result
    assert shoes_id in result


def test_mandatory_full_body_drops_non_mandatory_separates():
    mandatory_dress_id, shirt_id, pants_id, shoes_id = _ids(4)
    item_type_map = {
        mandatory_dress_id: "dress",
        shirt_id: "shirt",
        pants_id: "jeans",
        shoes_id: "boots",
    }
    result = deduplicate_by_body_slot(
        [shirt_id, mandatory_dress_id, pants_id, shoes_id],
        item_type_map,
        mandatory_item_ids={mandatory_dress_id},
    )
    assert mandatory_dress_id in result
    assert shirt_id not in result
    assert pants_id not in result
    assert shoes_id in result


def test_two_mandatory_items_in_one_role_do_not_both_survive():
    shirt_a, shirt_b, pants_id = _ids(3)
    item_type_map = {shirt_a: "t-shirt", shirt_b: "polo", pants_id: "jeans"}
    result = deduplicate_by_body_slot(
        [shirt_a, shirt_b, pants_id],
        item_type_map,
        mandatory_item_ids={shirt_a, shirt_b},
    )
    assert result == [shirt_a, pants_id]


def test_mandatory_full_body_and_mandatory_separates_do_not_coexist():
    dress_id, pants_id, shoes_id = _ids(3)
    item_type_map = {dress_id: "dress", pants_id: "jeans", shoes_id: "boots"}
    result = deduplicate_by_body_slot(
        [dress_id, pants_id, shoes_id],
        item_type_map,
        mandatory_item_ids={dress_id, pants_id},
    )
    assert result == [dress_id, shoes_id]


def test_mandatory_item_absent_from_candidates_does_not_empty_its_role():
    absent_shirt, shirt_id, pants_id = _ids(3)
    item_type_map = {shirt_id: "shirt", pants_id: "jeans"}
    result = deduplicate_by_body_slot(
        [shirt_id, pants_id],
        item_type_map,
        mandatory_item_ids={absent_shirt},
    )
    assert result == [shirt_id, pants_id]


def test_count_composition_buckets_by_role():
    c = count_composition(
        [
            ("cardigan", 8),
            ("shirt", 1),
            ("Sweater", 2),
            ("skirt", 4),
            ("dress", 3),
            ("jacket", 5),
            ("mystery", 7),
            (None, 1),
        ]
    )
    assert c == WardrobeComposition(base_tops=3, layers=8, bottoms=4, full_body=3)


def test_count_composition_covers_every_role_in_item_role():
    c = count_composition([(t, 1) for t in ITEM_ROLE])
    roles = list(ITEM_ROLE.values())
    assert c.base_tops == roles.count("base_top")
    assert c.layers == roles.count("mid_layer")
    assert c.bottoms == roles.count("bottom")
    assert c.full_body == roles.count("full_body")


def test_remove_legwear_keeps_requested_tights_and_everything_else():
    skirt, top, tights, requested_tights, shoes = _ids(5)
    item_type_map = {
        skirt: "skirt",
        top: "t-shirt",
        tights: "tights",
        requested_tights: "tights",
        shoes: "shoes",
    }
    kept = remove_legwear(
        [top, skirt, tights, requested_tights, shoes], item_type_map, {requested_tights}
    )
    assert kept == [top, skirt, requested_tights, shoes]


@pytest.mark.parametrize("subtype", ["overshirt", "Chore-Jacket", "shirt-jacket", "shacket"])
def test_an_overshirt_alone_lacks_a_top_under_it(subtype):
    overshirt, pants, shoes = _ids(3)
    types = {overshirt: "shirt", pants: "pants", shoes: "sneakers"}
    assert lacks_top_under_overshirt([overshirt, pants, shoes], types, {overshirt: subtype})


@pytest.mark.parametrize("under", ["t-shirt", "shirt", "sweater", "dress"])
def test_an_overshirt_over_a_top_or_dress_is_complete(under):
    overshirt, top, shoes = _ids(3)
    types = {overshirt: "shirt", top: under, shoes: "sneakers"}
    assert not lacks_top_under_overshirt([overshirt, top, shoes], types, {overshirt: "overshirt"})


def test_an_ordinary_shirt_alone_is_still_a_top():
    shirt, pants = _ids(2)
    types = {shirt: "shirt", pants: "pants"}
    assert not lacks_top_under_overshirt([shirt, pants], types, {shirt: "camp-collar"})


def test_the_top_added_under_an_overshirt_is_the_best_ranked_real_top():
    other_overshirt, sweater, tee, pants = _ids(4)
    types = {other_overshirt: "shirt", sweater: "sweater", tee: "t-shirt", pants: "pants"}
    subtypes = {other_overshirt: "chore-jacket"}
    ranked = [pants, other_overshirt, sweater, tee]
    assert top_to_wear_under(ranked, {pants}, types, subtypes) == sweater
    assert top_to_wear_under(ranked, {pants, sweater}, types, subtypes) == tee
    assert top_to_wear_under([pants, other_overshirt], set(), types, subtypes) is None


@pytest.mark.parametrize("thin_ranked", [True, False])
def test_layering_repair_preserves_the_rules_and_is_idempotent(thin_ranked):
    (
        overshirt,
        sweater,
        hoodie,
        cardigan,
        polo,
        tee,
        shirt,
        dress,
        jumpsuit,
        best_tee,
        pants,
        coat,
    ) = _ids(12)
    tops = [overshirt, sweater, hoodie, cardigan, polo, tee, shirt, dress, jumpsuit]
    types = dict(
        zip(
            [*tops, best_tee, pants, coat],
            [
                "shirt",
                "sweater",
                "hoodie",
                "cardigan",
                "polo",
                "t-shirt",
                "shirt",
                "dress",
                "jumpsuit",
                "t-shirt",
                "pants",
                "coat",
            ],
            strict=True,
        )
    )
    subtypes = {overshirt: "overshirt", shirt: "button-down"}
    ranked = [
        polo,
        best_tee,
        cardigan,
        jumpsuit,
        shirt,
        tee,
        sweater,
        hoodie,
        dress,
        coat,
        pants,
        overshirt,
    ]
    if not thin_ranked:
        ranked = [item_id for item_id in ranked if item_id not in {tee, best_tee}]
    for size in range(len(tops) + 1):
        for selected in combinations(tops, size):
            chosen = [*selected, pants, coat]
            for pins in [set(), *[{item_id} for item_id in selected]]:
                repaired = repair_top_layers(chosen, ranked, types, subtypes, pins)
                assert pins <= set(repaired)
                assert len(repaired) == len(set(repaired))
                if selected:
                    assert set(repaired) & {*tops, best_tee}
                else:
                    assert repaired == chosen
                assert len(set(repaired) & {*tops, best_tee, coat}) <= 3
                seated = deduplicate_by_body_slot(repaired, types, pins)
                assert seated == repaired
                if overshirt in repaired:
                    if thin_ranked or tee in selected:
                        assert set(repaired) & {tee, best_tee}
                        assert not {
                            sweater,
                            hoodie,
                            cardigan,
                            polo,
                            shirt,
                            dress,
                            jumpsuit,
                        }.intersection(repaired)
                    if thin_ranked and tee not in selected:
                        assert best_tee in repaired
                if {sweater, hoodie, cardigan}.intersection(repaired):
                    if polo not in pins:
                        assert set(repaired) & {tee, best_tee, shirt, dress, jumpsuit}
                assert repair_top_layers(repaired, ranked, types, subtypes, pins) == repaired


def test_layering_repair_handles_empty_and_missing_candidates():
    assert repair_top_layers([], [], {}, {}) == []
    unknown, sweater, overshirt, polo = _ids(4)
    types = {sweater: "sweater", overshirt: "shirt", polo: "polo"}
    subtypes = {overshirt: "Chore-Jacket"}
    assert repair_top_layers([unknown], [unknown], types, subtypes) == [unknown]
    assert repair_top_layers([sweater], [], types, {}) == [sweater]
    assert repair_top_layers([overshirt, polo], [], types, subtypes) == [polo]


@pytest.mark.parametrize("subtype", ["overshirt", "chore-jacket", "shirt-jacket", "shacket"])
def test_overshirts_use_henleys_but_not_button_shirts(subtype):
    overshirt, button_shirt, henley = _ids(3)
    types = {overshirt: "shirt", button_shirt: "shirt", henley: "shirt"}
    subtypes = {overshirt: subtype, button_shirt: "button-down", henley: "henley"}
    assert repair_top_layers(
        [overshirt, button_shirt], [button_shirt, henley], types, subtypes
    ) == [overshirt, henley]


def test_mixed_case_henley_is_eligible_under_an_overshirt():
    overshirt, henley, tee = _ids(3)
    types = {overshirt: "shirt", henley: "shirt", tee: "t-shirt"}
    assert repair_top_layers(
        [overshirt], [henley, tee], types, {overshirt: "overshirt", henley: "Henley"}
    ) == [overshirt, henley]


def test_overshirt_replacement_uses_highest_ranked_eligible_top():
    overshirt, polo, henley, tee = _ids(4)
    types = {overshirt: "shirt", polo: "polo", henley: "shirt", tee: "t-shirt"}
    subtypes = {overshirt: "overshirt", henley: "henley"}
    assert repair_top_layers([overshirt, polo], [polo, henley, tee], types, subtypes) == [
        overshirt,
        henley,
    ]


def test_pinned_layer_conflict_drops_extra_unpinned_outerwear():
    overshirt, hoodie, coat, tee = _ids(4)
    types = {overshirt: "shirt", hoodie: "hoodie", coat: "coat", tee: "t-shirt"}
    assert repair_top_layers(
        [overshirt, hoodie, coat],
        [tee],
        types,
        {overshirt: "overshirt"},
        mandatory_item_ids={overshirt, hoodie},
    ) == [overshirt, hoodie, tee]


@pytest.mark.parametrize("outer", ["coat", "sweater"])
def test_repair_preserves_a_shirt_seated_in_the_middle(outer):
    tee, shirt, pants, layer = _ids(4)
    types = {tee: "t-shirt", shirt: "shirt", pants: "pants", layer: outer}
    selected = deduplicate_by_body_slot([tee, shirt, pants, layer], types)
    assert repair_top_layers(selected, [tee, shirt, layer], types, {}) == selected


@pytest.mark.parametrize("full_body", ["dress", "jumpsuit"])
@pytest.mark.parametrize("layer", ["cardigan", "hoodie"])
def test_a_full_body_garment_covers_the_base_under_a_layer(full_body, layer):
    one_piece, over, tee = _ids(3)
    types = {one_piece: full_body, over: layer, tee: "t-shirt"}
    assert repair_top_layers([one_piece, over], [tee, one_piece, over], types, {}) == [
        one_piece,
        over,
    ]


def test_repair_uses_rank_order_instead_of_type_map_insertion_order():
    overshirt, polo, lower_ranked, best = _ids(4)
    types = {overshirt: "shirt", polo: "polo", lower_ranked: "t-shirt", best: "t-shirt"}
    assert repair_top_layers(
        [overshirt, polo],
        [best, lower_ranked, polo, overshirt],
        types,
        {overshirt: "overshirt"},
    ) == [overshirt, best]


def test_two_sweaters_keep_the_best_ranked_selected_layer():
    sweater, cardigan, tee = _ids(3)
    types = {sweater: "sweater", cardigan: "cardigan", tee: "t-shirt"}
    assert repair_top_layers([sweater, cardigan], [cardigan, tee, sweater], types, {}) == [
        cardigan,
        tee,
    ]


def test_fallback_sweater_gets_a_base_before_repair_finishes():
    overshirt, pants, sweater, shirt = _ids(4)
    types = {overshirt: "shirt", pants: "pants", sweater: "sweater", shirt: "shirt"}
    subtypes = {overshirt: "overshirt", shirt: "button-down"}
    ranked = [sweater, shirt, pants, overshirt]
    repaired = repair_top_layers([overshirt, pants], ranked, types, subtypes)
    assert repaired == [pants, sweater, shirt]
    assert repair_top_layers(repaired, ranked, types, subtypes) == repaired


@pytest.mark.parametrize("middle", ["cardigan", "vest"])
def test_three_pinned_layers_are_never_emptied_or_given_a_fourth(middle, caplog):
    sweater, layer, coat, tee = _ids(4)
    types = {sweater: "sweater", layer: middle, coat: "coat", tee: "t-shirt"}
    selected = [sweater, layer, coat]
    assert repair_top_layers(selected, [tee], types, {}, set(selected)) == selected
    assert "pinned" in caplog.text.lower()


def test_layer_cap_keeps_outerwear_and_removes_an_unpinned_mid_layer():
    sweater, vest, jacket, tee = _ids(4)
    types = {sweater: "sweater", vest: "vest", jacket: "jacket", tee: "t-shirt"}
    assert repair_top_layers([sweater, vest, jacket], [tee], types, {}) == [sweater, jacket, tee]
