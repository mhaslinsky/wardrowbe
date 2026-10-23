import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.inspiration_service import match_slots, parse_analysis


def garment(matched_id=None, **values):
    return {
        "type": "shirt",
        "color": "blue",
        "fit": "regular",
        "pattern": "solid",
        "item_id": str(matched_id) if matched_id else None,
        "match_strength": 0.9,
        **values,
    }


def analysis(slots, items):
    return parse_analysis(
        json.dumps({"slots": slots, "allowed_item_ids": [str(item.id) for item in items]})
    )


def item(kind="shirt", subtype=None):
    return SimpleNamespace(id=uuid4(), type=kind, subtype=subtype)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "{}",
        '{"slots":[],"allowed_item_ids":[]}',
        "null",
        '{"slots":[{}],"allowed_item_ids":[]}',
    ],
)
def test_reject_missing_empty_malformed_analysis(text):
    with pytest.raises(ValidationError):
        parse_analysis(text)


def test_parse_fenced_garments_and_strength():
    shirt = item()
    parsed = analysis([garment(shirt.id)], [shirt])
    assert parse_analysis("```json\n" + parsed.model_dump_json() + "\n```") == parsed
    slots, chosen = match_slots(parsed, [shirt])
    assert chosen == [shirt.id]
    assert slots[0].match_strength == 0.9 and not slots[0].no_match


@pytest.mark.parametrize(
    "change",
    [{"match_strength": 0.49}, {"no_match": True}, {"item_id": str(uuid4())}, {"type": "jeans"}],
)
def test_bad_matches_become_explicit_gaps(change):
    shirt = item()
    slots, chosen = match_slots(analysis([garment(shirt.id, **change)], [shirt]), [shirt])
    assert chosen == []
    assert slots[0].no_match and slots[0].item_id is None and slots[0].match_strength == 0


def test_no_wardrobe_and_rule_exclusions_are_gaps():
    shirt = item()
    for candidates in [[], [shirt]]:
        slots, chosen = match_slots(analysis([garment(shirt.id)], []), candidates)
        assert not chosen and slots[0].no_match


def test_layering_adds_base_and_prevents_reuse():
    sweater, base = item("sweater"), item("t-shirt")
    slots, chosen = match_slots(
        analysis(
            [garment(sweater.id, type="sweater"), garment(sweater.id, type="sweater")],
            [sweater, base],
        ),
        [sweater, base],
    )
    assert set(chosen) == {sweater.id, base.id}
    assert slots[1].no_match


def test_removed_layer_match_becomes_a_gap():
    jeans, pants = item("jeans"), item("pants")
    slots, chosen = match_slots(
        analysis(
            [garment(jeans.id, type="jeans"), garment(pants.id, type="pants")], [jeans, pants]
        ),
        [jeans, pants],
    )
    assert chosen == [jeans.id]
    assert slots[1].no_match and slots[1].item_id is None
