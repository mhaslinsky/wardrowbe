import json
import re
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.considering import ItemPurchase
from app.models.item import ItemStatus
from app.models.outfit import Outfit
from tests.test_considering_preview import fixture_items
from tests.test_considering_preview import remove_test_account as remove_test_account
from tests.test_recommendation_service import _weather

pytestmark = pytest.mark.asyncio


@pytest.fixture
def provider(test_user):
    test_user.location_lat = 40
    test_user.location_lon = -74
    ai = MagicMock()
    ai.generate_text = AsyncMock(
        return_value=MagicMock(
            content=json.dumps({"outfits": [{"items": [1, 2, 3, 999]}] * 3}),
            model="stub",
            endpoint="stub",
        )
    )
    with (
        patch("app.services.recommendation_service.AIService", return_value=ai),
        patch(
            "app.services.recommendation_service.WeatherService.get_current_weather",
            AsyncMock(return_value=_weather()),
        ),
        patch(
            "app.services.recommendation_service.pop_suggestion", AsyncMock(return_value=None)
        ) as pop,
        patch("app.services.recommendation_service.push_suggestions", AsyncMock()) as push,
    ):
        yield ai, pop, push


async def test_anchor_owned_companions_cache_and_lifecycle(
    client, auth_headers, db_session, test_user, provider
):
    anchor, shelf, pants, shoes, dirty = await fixture_items(db_session, test_user)
    shelf.type = "jacket"
    await db_session.commit()
    ai, pop, push = provider

    async def choose_from_prompt(prompt, **_kwargs):
        numbers = [int(number) for number in re.findall(r"^\[(\d+)\] ", prompt, re.MULTILINE)]
        return MagicMock(
            content=json.dumps({"outfits": [{"items": numbers}] * 3}), model="stub", endpoint="stub"
        )

    ai.generate_text.side_effect = choose_from_prompt
    response = await client.post(
        f"/api/v1/considering/{anchor.id}/suggestions", headers=auth_headers, json={}
    )
    assert response.status_code == 201, response.text
    outfits = response.json()
    assert len(outfits) == 3
    allowed = {str(anchor.id), str(pants.id), str(shoes.id), str(dirty.id)}
    for outfit in outfits:
        ids = [item["id"] for item in outfit["items"]]
        assert ids.count(str(anchor.id)) == 1
        assert len(ids) >= 2 and set(ids) <= allowed
        assert outfit["unowned_item_ids"] == [str(anchor.id)]
    ai.generate_text.assert_awaited_once()
    pop.assert_not_awaited()
    push.assert_not_awaited()
    outfit_id = outfits[0]["id"]
    user_id = test_user.id
    from uuid import UUID

    from app.models.learning import ItemPairScore, OutfitPerformance, UserLearningProfile
    from app.services.learning_service import LearningService

    async def learning_rows():
        return [
            list(
                (
                    await db_session.execute(
                        select(model.__table__).where(model.user_id == user_id)
                    )
                ).all()
            )
            for model in (UserLearningProfile, OutfitPerformance, ItemPairScore)
        ]

    baseline = await learning_rows()
    await LearningService(db_session).process_feedback(UUID(outfit_id), user_id)
    assert await learning_rows() == baseline
    refused = await client.post(
        f"/api/v1/outfits/{outfit_id}/wore",
        headers=auth_headers,
        json={"date": outfits[0]["scheduled_for"]},
    )
    assert refused.status_code == 403
    for action in ("passed", "restore", "bought"):
        transition = await client.post(
            f"/api/v1/considering/{anchor.id}/{action}",
            headers=auth_headers,
            json={"reason": "Fit"} if action == "passed" else None,
        )
        assert transition.status_code == 200
        saved = await client.get(f"/api/v1/outfits/{outfit_id}", headers=auth_headers)
        assert saved.json()["unowned_item_ids"] == ([] if action == "bought" else [str(anchor.id)])
        if action != "bought":
            await LearningService(db_session).process_feedback(UUID(outfit_id), user_id)
            assert await learning_rows() == baseline
    bought = await client.post(
        f"/api/v1/considering/{anchor.id}/suggestions", headers=auth_headers, json={}
    )
    assert bought.status_code == 201, bought.text
    assert all(not outfit["unowned_item_ids"] for outfit in bought.json())
    worn = await client.post(
        f"/api/v1/outfits/{outfit_id}/wore",
        headers=auth_headers,
        json={"date": outfits[0]["scheduled_for"]},
    )
    assert worn.status_code == 200, worn.text
    await db_session.refresh(anchor)
    assert anchor.wear_count == 1
    assert await learning_rows() != baseline


@pytest.mark.parametrize(
    "invalid", ["missing", "passed", "unready", "archived", "underwear", "foreign"]
)
async def test_invalid_anchor_never_calls_provider(
    client, auth_headers, db_session, test_user, provider, invalid
):
    anchor, underwear, *_rest = await fixture_items(db_session, test_user)
    expected = 422
    if invalid == "missing":
        anchor_id = uuid4()
        expected = 404
    else:
        if invalid == "passed":
            (await db_session.get(ItemPurchase, anchor.id)).state = "passed"
            expected = 409
        elif invalid == "unready":
            anchor.status = ItemStatus.processing
        elif invalid == "archived":
            anchor.is_archived = True
        elif invalid == "underwear":
            anchor = underwear
            expected = 400
        elif invalid == "foreign":
            from app.models.user import User
            from tests.identity_helpers import add_users_with_identities

            other = User(id=uuid4(), email=f"{uuid4()}@example.invalid", display_name="Other")
            other.external_id = str(other.id)
            await add_users_with_identities(db_session, other)
            anchor.user_id = other.id
            expected = 404
        anchor_id = anchor.id
        await db_session.commit()
    response = await client.post(
        f"/api/v1/considering/{anchor_id}/suggestions", headers=auth_headers, json={}
    )
    assert response.status_code == expected, response.text
    provider[0].generate_text.assert_not_awaited()


async def test_auth_schema_and_ordinary_include_remain_owned_only(
    client, auth_headers, db_session, test_user, provider
):
    anchor, *_rest = await fixture_items(db_session, test_user)
    path = f"/api/v1/considering/{anchor.id}/suggestions"
    assert (await client.post(path, json={})).status_code == 401
    for payload in ({"include_items": [str(anchor.id)]}, {"occasion": "invalid"}):
        assert (await client.post(path, headers=auth_headers, json=payload)).status_code == 422
    response = await client.post(
        "/api/v1/outfits/suggest-options",
        headers=auth_headers,
        json={"include_items": [str(anchor.id)]},
    )
    assert response.status_code == 400, response.text
    provider[0].generate_text.assert_not_awaited()


@pytest.mark.parametrize("content", ["broken", '{"outfits": []}', '{"outfits": [{"items": []}]}'])
async def test_malformed_provider_leaves_no_outfits(
    client, auth_headers, db_session, test_user, provider, content
):
    anchor, *_rest = await fixture_items(db_session, test_user)
    provider[0].generate_text.return_value.content = content
    response = await client.post(
        f"/api/v1/considering/{anchor.id}/suggestions", headers=auth_headers, json={}
    )
    assert response.status_code == 503, response.text
    assert not list(
        await db_session.scalars(select(Outfit.id).where(Outfit.user_id == test_user.id))
    )


async def test_empty_owned_candidates_and_disabled_ai(
    client, auth_headers, db_session, test_user, provider
):
    from app.services.ai_service import AIDisabledError

    anchor, _shelf, *owned = await fixture_items(db_session, test_user)
    for item in owned:
        item.is_archived = True
    await db_session.commit()
    path = f"/api/v1/considering/{anchor.id}/suggestions"
    response = await client.post(path, headers=auth_headers, json={})
    assert response.status_code == 400, response.text
    provider[0].generate_text.assert_not_awaited()
    with patch(
        "app.services.recommendation_service.require_internal_ai",
        side_effect=AIDisabledError("disabled"),
    ):
        response = await client.post(path, headers=auth_headers, json={})
    assert response.status_code == 503
    assert response.json()["detail"] == "Internal AI is disabled"
    provider[0].generate_text.assert_not_awaited()


async def test_forced_shelf_companion_is_rejected_by_validation(db_session, test_user):
    from fastapi import HTTPException

    from app.services.considering_preview_service import ConsideringPreviewService

    anchor, shelf, *_rest = await fixture_items(db_session, test_user)
    shelf.type = "jacket"
    await db_session.commit()
    with pytest.raises(HTTPException) as refused:
        await ConsideringPreviewService(db_session).validate(test_user.id, anchor.id, [shelf.id])
    assert refused.value.status_code == 422


async def test_materialize_rejects_forced_shelf_companion(db_session, test_user):
    from app.models.outfit import OutfitSource
    from app.services.recommendation_service import EmptyOutfitError, RecommendationService

    anchor, shelf, pants, *_rest = await fixture_items(db_session, test_user)
    shelf.type = "jacket"
    await db_session.commit()
    service = RecommendationService(db_session)
    service._defer_commit = True
    with pytest.raises(EmptyOutfitError):
        await service._materialize_outfit(
            {"items": [1, 2, 3]},
            test_user,
            _weather(),
            "casual",
            OutfitSource.on_demand,
            {1: anchor.id, 2: pants.id, 3: shelf.id},
            mandatory_item_ids={anchor.id},
            considering_anchor_id=anchor.id,
        )
    assert not list(
        await db_session.scalars(select(Outfit.id).where(Outfit.user_id == test_user.id))
    )


async def test_anchor_cache_isolation_without_deferred_commit(db_session, test_user, provider):
    from app.services.recommendation_service import RecommendationService

    anchor, *_rest = await fixture_items(db_session, test_user)
    await db_session.refresh(test_user, ["preferences"])
    service = RecommendationService(db_session)
    assert service._defer_commit is False
    outfits = await service.generate_recommendations(
        test_user, "casual", weather_override=_weather(), considering_anchor_id=anchor.id
    )
    assert len(outfits) == 1
    assert any(item.item_id == anchor.id for item in outfits[0].items)
    provider[1].assert_not_awaited()
    provider[2].assert_not_awaited()


async def test_invalid_provider_option_is_skipped_without_losing_valid_options(
    client, auth_headers, db_session, test_user, provider
):
    anchor, _shelf, pants, shoes, *_rest = await fixture_items(db_session, test_user)
    for item, name in [(anchor, "Anchor shirt"), (pants, "Owned pants"), (shoes, "Changing shoes")]:
        item.name = name
    await db_session.commit()

    async def choose_then_archive(prompt, **_kwargs):
        numbers = {
            name: int(number)
            for number, name in re.findall(r'^\[(\d+)\] "([^"]+)"', prompt, re.MULTILINE)
        }
        shoes.is_archived = True
        await db_session.flush()
        bad = [numbers[name] for name in ("Anchor shirt", "Owned pants", "Changing shoes")]
        good = [numbers[name] for name in ("Anchor shirt", "Owned pants")]
        return MagicMock(
            content=json.dumps({"outfits": [{"items": bad}, {"items": good}]}),
            model="stub",
            endpoint="stub",
        )

    provider[0].generate_text.side_effect = choose_then_archive
    response = await client.post(
        f"/api/v1/considering/{anchor.id}/suggestions", headers=auth_headers, json={}
    )
    assert response.status_code == 201, response.text
    assert len(response.json()) == 1
    assert {item["id"] for item in response.json()[0]["items"]} == {str(anchor.id), str(pants.id)}
    assert (
        len(list(await db_session.scalars(select(Outfit.id).where(Outfit.user_id == test_user.id))))
        == 1
    )
