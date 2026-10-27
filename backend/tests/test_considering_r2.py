from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.models.item import ClothingItem, ItemStatus
from app.models.learning import ItemPairScore, OutfitPerformance
from app.models.outfit import OutfitStatus, UserFeedback
from app.services.external_outfit_service import ExternalOutfitService
from app.services.learning_service import LearningService
from app.services.outfit_service import OutfitListFilters, OutfitService
from app.services.preview_service import PreviewService
from app.services.recommendation_service import RecommendationService
from tests.test_considering_r1 import purchase

pytestmark = pytest.mark.asyncio
PATH = "/api/v1/considering"


async def authored(db, user, item, *, feedback=False):
    outfit = await ExternalOutfitService(db).create_suggestion(
        user, item_ids=[item.id], occasion="casual"
    )
    if feedback:
        outfit.status = OutfitStatus.accepted
        db.add(
            UserFeedback(outfit_id=outfit.id, accepted=True, rating=3, comment="Sleeves are narrow")
        )
    await db.commit()
    return outfit


@pytest.mark.parametrize("state", ["considering", "passed"])
@pytest.mark.parametrize("shelf_source", [True, False])
async def test_pairing_lists_hide_shelf_members_and_counts(
    client, auth_headers, db_session, test_user, state, shelf_source
):
    shelf, _ = await purchase(db_session, test_user, state)
    owned = ClothingItem(
        user_id=test_user.id, type="pants", image_path="owned.jpg", status=ItemStatus.ready
    )
    db_session.add(owned)
    await db_session.commit()
    source, partner = (shelf, owned) if shelf_source else (owned, shelf)
    saved = await client.post(
        f"/api/v1/pairings/item/{source.id}",
        headers=auth_headers,
        json={"items": [str(partner.id)]},
    )
    assert saved.status_code == 201, saved.text
    for route in ["/api/v1/pairings", f"/api/v1/pairings/item/{source.id}"]:
        response = await client.get(route, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["total"] == 0
        assert response.json()["pairings"] == []
    assert (
        await client.get(f"/api/v1/outfits/{saved.json()['id']}", headers=auth_headers)
    ).status_code == 200
    assert (await PreviewService(db_session).outfit(UUID(saved.json()["id"]), test_user.id)).id
    if state == "passed":
        assert (
            await client.post(f"{PATH}/{shelf.id}/restore", headers=auth_headers)
        ).status_code == 200
    assert (await client.post(f"{PATH}/{shelf.id}/bought", headers=auth_headers)).status_code == 200
    assert (await client.get("/api/v1/pairings", headers=auth_headers)).json()["total"] == 1


@pytest.mark.parametrize("state", ["considering", "passed"])
@pytest.mark.parametrize("status", ["processing", "ready", "error"])
async def test_tagging_progress_excludes_all_shelf_phases(
    client, auth_headers, db_session, test_user, state, status
):
    item, _ = await purchase(db_session, test_user, state)
    now = datetime.now(UTC)
    item.status = ItemStatus(status)
    item.ai_started_at = now
    if status == "ready":
        item.ai_completed_at = now
    if status == "error":
        item.ai_failed_at = now
    db_session.add(
        ClothingItem(
            user_id=test_user.id, type="pants", image_path="owned.jpg", status=ItemStatus.processing
        )
    )
    await db_session.commit()
    response = await client.get("/api/v1/items/tagging-progress", headers=auth_headers)
    assert response.status_code == 200
    progress = response.json()
    assert progress["total"] == progress["queued"] == 1
    assert progress["analyzing"] == progress["failed"] == progress["completed"] == 0
    assert progress["current"] == progress["recent"] == progress["failures"] == []


async def test_considering_rate_limit_prevents_network(client, auth_headers, test_user):
    with (
        patch(
            "app.api.considering.rate_limit_by_user",
            AsyncMock(side_effect=HTTPException(429, "Too many requests")),
            create=True,
        ) as limiter,
        patch(
            "app.api.considering.download_image",
            AsyncMock(side_effect=HTTPException(418, "Download attempted")),
        ) as download,
    ):
        response = await client.post(
            PATH, headers=auth_headers, json={"image_urls": ["https://example.com/item.png"]}
        )
    assert response.status_code == 429
    limiter.assert_awaited_once_with(
        str(test_user.id), "considering", max_requests=5, window_seconds=60
    )
    download.assert_not_awaited()


async def test_source_url_column_limit_on_create_and_edit(
    client, auth_headers, db_session, test_user
):
    item, _ = await purchase(db_session, test_user)
    url = "https://example.com/" + "a" * (2001 - len("https://example.com/"))
    with patch(
        "app.api.considering.download_image",
        AsyncMock(side_effect=HTTPException(418, "Download attempted")),
    ) as download:
        response = await client.post(
            PATH,
            headers=auth_headers,
            json={"image_urls": ["https://example.com/item.png"], "source_url": url},
        )
    assert response.status_code == 422
    download.assert_not_awaited()
    assert (
        await client.patch(f"{PATH}/{item.id}", headers=auth_headers, json={"source_url": url})
    ).status_code == 422
    assert (
        await client.patch(f"{PATH}/{item.id}", headers=auth_headers, json={"source_url": url[:-1]})
    ).status_code == 200


async def test_outfit_analytics_excludes_shelf_outfits(client, auth_headers, db_session, test_user):
    item, _ = await purchase(db_session, test_user)
    await authored(db_session, test_user, item, feedback=True)
    response = await client.get("/api/v1/analytics", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["wardrobe"]["total_outfits"] == 0
    assert body["wardrobe"]["average_rating"] is None
    assert all(entry["total"] == 0 for entry in body["acceptance_trend"])


async def test_ready_to_wear_excludes_shelf_but_general_browsing_keeps_it(db_session, test_user):
    item, _ = await purchase(db_session, test_user)
    outfit = await authored(db_session, test_user, item)
    service = OutfitService(db_session)
    assert (
        await service.get_ids_by_filter(OutfitListFilters(user_id=test_user.id, ready_to_wear=True))
        == []
    )
    assert await service.get_ids_by_filter(OutfitListFilters(user_id=test_user.id)) == [outfit.id]


@pytest.mark.parametrize(
    "query", ["notes", "combinations", "performance", "profile", "count", "rebuild", "rejected"]
)
async def test_shelf_feedback_is_not_owned_learning_or_recommendation_input(
    db_session, test_user, query
):
    item, _ = await purchase(db_session, test_user)
    owned = ClothingItem(
        user_id=test_user.id, type="pants", image_path="owned.jpg", status=ItemStatus.ready
    )
    db_session.add(owned)
    await db_session.flush()
    outfit = await ExternalOutfitService(db_session).create_suggestion(
        test_user, item_ids=[item.id, owned.id], occasion="casual"
    )
    outfit.status = OutfitStatus.accepted
    db_session.add(
        UserFeedback(outfit_id=outfit.id, accepted=True, rating=3, comment="Sleeves are narrow")
    )
    await db_session.commit()
    recommendations = RecommendationService(db_session)
    learning = LearningService(db_session)
    if query == "notes":
        assert await recommendations._get_recent_outfit_notes(test_user) == []
    elif query == "combinations":
        assert await recommendations._get_recently_suggested_outfit_combinations(test_user) == set()
    elif query == "performance":
        await learning.process_feedback(outfit.id, test_user.id)
        assert await db_session.get(OutfitPerformance, outfit.id) is None
    elif query == "profile":
        learning.MIN_FEEDBACK_FOR_LEARNING = 1
        profile = await learning._recompute_profile(test_user.id)
        assert profile.feedback_count == 0
    elif query == "count":
        assert await learning._get_feedback_count(test_user.id) == 0
    elif query == "rebuild":
        await learning.rebuild_all_pair_scores(test_user.id)
        from sqlalchemy import select

        assert (await db_session.scalars(select(ItemPairScore))).all() == []
    else:
        from app.utils.timezone import get_user_today

        outfit.status = OutfitStatus.rejected
        outfit.scheduled_for = get_user_today(test_user)
        await db_session.commit()
        assert await recommendations._get_today_rejected_item_ids(test_user, "casual") == set()


@pytest.mark.parametrize("consumer", ["learning", "recommendation"])
async def test_learned_pairs_exclude_shelf_at_either_end(db_session, test_user, consumer):
    shelf, _ = await purchase(db_session, test_user)
    owned = ClothingItem(
        user_id=test_user.id, type="pants", image_path="owned.jpg", status=ItemStatus.ready
    )
    db_session.add(owned)
    await db_session.flush()
    db_session.add(
        ItemPairScore(
            user_id=test_user.id,
            item1_id=shelf.id,
            item2_id=owned.id,
            compatibility_score=0.9,
            times_paired=3,
        )
    )
    await db_session.commit()
    if consumer == "learning":
        assert (
            await LearningService(db_session).get_item_pair_suggestions(test_user.id, shelf.id)
            == []
        )
    else:
        assert await RecommendationService(db_session)._get_good_item_pairs(test_user.id) == {}


@pytest.mark.parametrize("retry", [False, True])
async def test_notification_delivery_rejects_shelf_outfit(db_session, test_user, retry):
    from app.models.notification import Notification
    from app.services.notification_service import NotificationDispatcher

    item, _ = await purchase(db_session, test_user)
    outfit = await authored(db_session, test_user, item)
    dispatcher = NotificationDispatcher(db_session)
    with patch.object(dispatcher, "deliver", AsyncMock(return_value=[])) as deliver:
        if retry:
            result = await dispatcher.retry_notification(
                Notification(user_id=test_user.id, outfit_id=outfit.id, channel="ntfy")
            )
            assert result.error == "Outfit not found"
        else:
            with pytest.raises(ValueError, match="Outfit not found"):
                await dispatcher.send_outfit_notification(test_user.id, outfit.id)
    deliver.assert_not_awaited()


async def test_ha_laundry_excludes_shelf_rows(client, db_session, test_user):
    from tests.test_ha_actions import TOKEN, _ha_settings

    item, _ = await purchase(db_session, test_user)
    item.needs_wash = True
    await db_session.commit()
    with _ha_settings(test_user):
        response = await client.post("/api/v1/ha/laundry-done", headers={"x-ha-token": TOKEN})
    assert response.status_code == 200
    assert response.json()["washed"] == 0
    await db_session.refresh(item)
    assert item.needs_wash


async def test_wash_reminder_excludes_shelf_rows(db_session, test_user):
    from app.workers.notifications import _check_wash_reminders_inner

    item, _ = await purchase(db_session, test_user)
    item.needs_wash = True
    await db_session.commit()
    with (
        patch("app.workers.notifications.get_db_session", return_value=db_session),
        patch(
            "app.services.notification_service.NotificationDispatcher.deliver",
            AsyncMock(return_value=[]),
        ) as deliver,
        patch.object(db_session, "close", AsyncMock()),
    ):
        result = await _check_wash_reminders_inner({})
    assert result == {"notified": 0}
    assert str(test_user.id) not in [call.args[0] for call in deliver.await_args_list]
