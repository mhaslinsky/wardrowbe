import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.utils.clothing import deduplicate_by_body_slot

ROOT = Path(__file__).resolve().parents[2]
NEW_BOTTOMS = ("chinos", "trousers", "joggers", "sweatpants")
LOCALES = ["de", "en", "fr", "it", "ja", "ko", "zh-CN", "zh-TW"]


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
