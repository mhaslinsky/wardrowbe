import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.item import ClothingItem
from app.services.recommendation_service import has_dressiness_clash
from app.utils.clothing import deduplicate_by_body_slot
from app.utils.garment_vocabulary import DEFAULT_WASH_INTERVALS, ITEM_ROLE

ROOT = Path(__file__).resolve().parents[2]
NEW_BOTTOMS = {"chinos": 4, "trousers": 4, "joggers": 3, "sweatpants": 3}
LOCALES = ["de", "en", "fr", "it", "ja", "ko", "zh-CN", "zh-TW"]


def _item(item_type: str, formality: str | None) -> ClothingItem:
    return ClothingItem(
        id=uuid4(), user_id=uuid4(), type=item_type, formality=formality, image_path="t.jpg"
    )


def _clash(*items: ClothingItem) -> bool:
    return has_dressiness_clash([item.id for item in items], {item.id: item for item in items})


@pytest.mark.parametrize("item_type", sorted(NEW_BOTTOMS))
def test_new_bottom_types_carry_role_wash_interval_and_generated_entry(item_type):
    assert ITEM_ROLE[item_type] == "bottom"
    assert DEFAULT_WASH_INTERVALS[item_type] == NEW_BOTTOMS[item_type]
    generated = (ROOT / "frontend/lib/generated/garment-vocabulary.ts").read_text()
    assert f"  {item_type}: 'bottom'," in generated


@pytest.mark.parametrize("locale", LOCALES)
def test_every_locale_names_the_new_bottom_types(locale):
    constants = json.loads((ROOT / f"frontend/messages/{locale}/constants.json").read_text())
    assert all(constants["types"].get(item_type) for item_type in NEW_BOTTOMS)


@pytest.mark.parametrize(
    ("first", "second"),
    [("chinos", "trousers"), ("joggers", "pants"), ("sweatpants", "jeans"), ("shorts", "chinos")],
)
def test_a_new_bottom_type_is_the_outfits_one_bottom(first, second):
    shirt, kept, dropped = uuid4(), uuid4(), uuid4()
    item_type_map = {shirt: "shirt", kept: first, dropped: second}
    assert deduplicate_by_body_slot([shirt, kept, dropped], item_type_map) == [shirt, kept]


def test_trousers_without_formality_sit_dressier_than_chinos():
    assert _clash(_item("trousers", None), _item("t-shirt", "casual"))
    assert not _clash(_item("chinos", None), _item("t-shirt", "casual"))
    assert not _clash(_item("trousers", "casual"), _item("t-shirt", "casual"))


@pytest.mark.parametrize("item_type", ["joggers", "sweatpants"])
def test_joggers_and_sweatpants_without_formality_are_very_casual(item_type):
    assert _clash(_item(item_type, None), _item("shirt", "smart-casual"))
    assert not _clash(_item(item_type, None), _item("t-shirt", "casual"))
    assert not _clash(_item("pants", None), _item("shirt", "smart-casual"))
