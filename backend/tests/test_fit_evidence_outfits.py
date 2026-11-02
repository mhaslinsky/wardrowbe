from uuid import uuid4

import pytest

from app.models.item import ClothingItem
from app.services.fit_evidence_outfits import outfit_ideas, outfit_pool


def garments(*kinds):
    return [ClothingItem(id=uuid4(), type=kind) for kind in kinds]


@pytest.mark.parametrize("excluded_type", ["robe", " RoBe ", "glasses"])
def test_suggestion_excluded_items_never_become_buying_check_companions(excluded_type):
    shirt, pants, shoes, excluded = garments("shirt", "pants", "shoes", excluded_type)
    pool, eligible_count = outfit_pool([pants, shoes, excluded])
    assert pool == [pants, shoes]
    assert eligible_count == 2
    result = outfit_ideas(shirt, pool, eligible_count)
    assert result["count"] == 1
    assert {item_id for idea in result["ideas"] for item_id in idea["item_ids"]} == {
        str(shirt.id),
        str(pants.id),
        str(shoes.id),
    }


@pytest.mark.parametrize("excluded_type", ["robe", " RoBe ", "glasses"])
def test_suggestion_excluded_anchor_has_no_buying_check_outfit_ideas(excluded_type):
    anchor, shirt, pants, shoes = garments(excluded_type, "shirt", "pants", "shoes")
    pool, eligible_count = outfit_pool([shirt, pants, shoes])
    result = outfit_ideas(anchor, pool, eligible_count)
    assert result["status"] == "not_applicable"
    assert result["count"] == 0
    assert result["ideas"] == []
