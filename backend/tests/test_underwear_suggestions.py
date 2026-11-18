from uuid import uuid4

import pytest

from app.models.item import ClothingItem, ItemStatus
from app.services.recommendation_service import (
    RECOMMENDATION_PROMPT,
    RECOMMENDATION_PROMPT_WITH_UNDERWEAR,
    RecommendationService,
)
from app.utils.clothing import keep_one_underwear
from tests.recommendation_helpers import generation_fakes
from tests.test_item_scorer import _weather


def test_prompt_lists_underwear_only_when_switched_on():
    assert "underwear" not in RECOMMENDATION_PROMPT.lower()
    assert "at most ONE underwear piece (underwear)" in RECOMMENDATION_PROMPT_WITH_UNDERWEAR
    assert "<<" not in RECOMMENDATION_PROMPT_WITH_UNDERWEAR


def test_keep_one_underwear_keeps_first_or_mandatory():
    first, shirt, second = uuid4(), uuid4(), uuid4()
    types = {first: "underwear", shirt: "shirt", second: "underwear"}
    assert keep_one_underwear([first, shirt, second], types, set()) == [first, shirt]
    assert keep_one_underwear([first, shirt, second], types, {second}) == [shirt, second]


@pytest.mark.parametrize("switch", [False, True])
async def test_suggestion_follows_underwear_switch(
    client, auth_headers, db_session, test_user, switch
):
    response = await client.patch(
        "/api/v1/users/me/preferences", headers=auth_headers, json={"suggest_underwear": switch}
    )
    assert response.status_code == 200
    await db_session.refresh(test_user, ["preferences"])
    items = [
        ClothingItem(
            user_id=test_user.id,
            type=kind,
            image_path=f"test/{uuid4()}.jpg",
            status=ItemStatus.ready,
            primary_color="black",
        )
        for kind in ("t-shirt", "jeans", "sneakers", "underwear", "underwear")
    ]
    db_session.add_all(items)
    await db_session.commit()
    number_map = {number: item.id for number, item in enumerate(items, start=1)}
    service = RecommendationService(db_session)
    with generation_fakes(service, {"items": [1, 2, 3, 5, 4]}, number_map) as (ai, _, _):
        (outfit,) = await service.generate_recommendations(
            user=test_user, occasion="casual", weather_override=_weather(), single_outfit=True
        )
        scored = service._format_items_for_prompt.call_args.args[0]
    assert ("underwear" in {entry.item.type for entry in scored}) is switch
    assert ("underwear" in ai.generate_text.call_args.args[0]) is switch
    chosen_underwear = [link.item_id for link in outfit.items if link.item.type == "underwear"]
    assert chosen_underwear == ([items[4].id] if switch else [])
    saved = await service._day_suggestions(test_user.id, outfit.scheduled_for)
    assert [entry.id for entry in saved] == [outfit.id]
