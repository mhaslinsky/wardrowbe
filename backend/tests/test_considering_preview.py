from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.models.considering import ItemPurchase
from app.models.item import ClothingItem, ItemStatus
from app.models.outfit import Outfit, UserFeedback

pytestmark = pytest.mark.asyncio
PATH = "/api/v1/considering"


@pytest.fixture(autouse=True)
async def remove_test_account(db_session, test_user):
    from app.models.user import User

    user_id = test_user.id
    yield
    await db_session.rollback()
    await db_session.execute(delete(User).where(User.id == user_id))
    await db_session.commit()


async def fixture_items(db, user):
    items = [
        ClothingItem(user_id=user.id, type=kind, image_path="fixture.png", status=ItemStatus.ready)
        for kind in ("shirt", "underwear", "pants", "shoes", "shirt")
    ]
    db.add_all(items)
    await db.flush()
    db.add_all([ItemPurchase(item_id=item.id) for item in items[:2]])
    items[4].needs_wash = True
    await db.commit()
    return items


async def test_direct_preview_membership_and_lifecycle(client, auth_headers, db_session, test_user):
    anchor, underwear, pants, shoes, dirty = await fixture_items(db_session, test_user)
    for selected, companions in ((anchor, []), (underwear, []), (anchor, [pants, shoes, dirty])):
        response = await client.post(
            f"{PATH}/{selected.id}/outfits",
            headers=auth_headers,
            json={"companion_ids": [str(item.id) for item in companions]},
        )
        assert response.status_code == 201, response.text
        saved = response.json()
        assert {item["id"] for item in saved["items"]} == {
            str(selected.id),
            *(str(item.id) for item in companions),
        }
        assert saved["unowned_item_ids"] == [str(selected.id)]
        outfit_id = saved["id"]
    for action, body in (("passed", {"reason": "Fit"}), ("restore", None), ("bought", None)):
        assert (
            await client.post(f"{PATH}/{anchor.id}/{action}", headers=auth_headers, json=body)
        ).status_code == 200
        saved = (await client.get(f"/api/v1/outfits/{outfit_id}", headers=auth_headers)).json()
        assert saved["unowned_item_ids"] == ([] if action == "bought" else [str(anchor.id)])
    listing = (await client.get("/api/v1/outfits", headers=auth_headers)).json()["outfits"]
    assert next(row for row in listing if row["id"] == outfit_id)["unowned_item_ids"] == []


@pytest.mark.parametrize(
    "invalid",
    [
        "duplicate",
        "anchor",
        "shelf",
        "missing",
        "malformed",
        "unready",
        "archived",
        "passed",
        "foreign",
        "too_many",
    ],
)
async def test_direct_preview_rejects_invalid_companions(
    client, auth_headers, db_session, test_user, invalid
):
    from app.models.user import User
    from tests.identity_helpers import add_users_with_identities

    anchor, shelf, companion, _, _ = await fixture_items(db_session, test_user)
    ids = [str(companion.id)]
    if invalid == "duplicate":
        ids *= 2
    elif invalid == "anchor":
        ids = [str(anchor.id)]
    elif invalid == "shelf":
        ids = [str(shelf.id)]
    elif invalid == "missing":
        ids = [str(uuid4())]
    elif invalid == "malformed":
        ids = ["bad-id"]
    elif invalid == "too_many":
        ids = [str(uuid4()) for _index in range(20)]
    elif invalid == "unready":
        companion.status = ItemStatus.processing
    elif invalid == "archived":
        companion.is_archived = True
    elif invalid == "passed":
        db_session.add(ItemPurchase(item_id=companion.id, state="passed"))
    elif invalid == "foreign":
        other = User(id=uuid4(), email=f"{uuid4()}@example.invalid", display_name="Other")
        other.external_id = str(other.id)
        await add_users_with_identities(db_session, other)
        companion.user_id = other.id
    await db_session.commit()
    before = await db_session.scalar(
        select(func.count()).select_from(Outfit).where(Outfit.user_id == test_user.id)
    )
    response = await client.post(
        f"{PATH}/{anchor.id}/outfits", headers=auth_headers, json={"companion_ids": ids}
    )
    assert response.status_code == 422, response.text
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Outfit).where(Outfit.user_id == test_user.id)
        )
        == before
    )


async def test_direct_preview_auth_anchor_and_wear_safeguards(
    client, auth_headers, db_session, test_user
):
    anchor, _, companion, _, _ = await fixture_items(db_session, test_user)
    route = f"{PATH}/{anchor.id}/outfits"
    assert (await client.post(route, json={})).status_code == 401
    assert (
        await client.post(f"{PATH}/{uuid4()}/outfits", headers=auth_headers, json={})
    ).status_code == 404
    response = await client.post(route, headers=auth_headers, json={})
    assert response.status_code == 201, response.text
    outfit_id = response.json()["id"]
    for suffix, payload in (
        ("wore", {"date": date.today().isoformat()}),
        ("feedback", {"worn": True, "accepted": True}),
    ):
        rejected = await client.post(
            f"/api/v1/outfits/{outfit_id}/{suffix}", headers=auth_headers, json=payload
        )
        assert rejected.status_code in (403, 422), rejected.text
    rejected = await client.post(
        "/api/v1/outfits/studio",
        headers=auth_headers,
        json={
            "items": [str(anchor.id), str(companion.id)],
            "occasion": "casual",
            "mark_worn": True,
        },
    )
    assert rejected.status_code in (403, 422), rejected.text
    await db_session.rollback()
    await db_session.refresh(test_user)
    await db_session.refresh(anchor)
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(UserFeedback)
            .join(Outfit)
            .where(Outfit.user_id == test_user.id, UserFeedback.worn_at.is_not(None))
        )
        == 0
    )
    assert (
        await client.post(
            f"{PATH}/{anchor.id}/passed", headers=auth_headers, json={"reason": "Fit"}
        )
    ).status_code == 200
    assert (await client.post(route, headers=auth_headers, json={})).status_code == 409


async def test_preview_learning_stays_unchanged_until_bought(
    client, auth_headers, db_session, test_user
):
    from app.models.learning import ItemPairScore, OutfitPerformance, UserLearningProfile
    from app.services.learning_service import LearningService

    anchor, _, companion, _, _ = await fixture_items(db_session, test_user)
    response = await client.post(
        f"{PATH}/{anchor.id}/outfits",
        headers=auth_headers,
        json={"companion_ids": [str(companion.id)]},
    )
    assert response.status_code == 201, response.text
    outfit_id = response.json()["id"]
    from uuid import UUID

    async def learning_rows():
        return [
            list(
                (
                    await db_session.execute(
                        select(model.__table__).where(model.user_id == test_user.id)
                    )
                ).all()
            )
            for model in (UserLearningProfile, OutfitPerformance, ItemPairScore)
        ]

    await db_session.commit()
    baseline = await learning_rows()
    for action in (None, "passed", "restore"):
        if action:
            transition = await client.post(
                f"{PATH}/{anchor.id}/{action}",
                headers=auth_headers,
                json={"reason": "Fit"} if action == "passed" else None,
            )
            assert transition.status_code == 200
            await db_session.commit()
        await LearningService(db_session).process_feedback(UUID(outfit_id), test_user.id)
        assert await learning_rows() == baseline
        missing_photo = await client.post(
            f"/api/v1/outfits/{outfit_id}/preview", headers=auth_headers
        )
        await db_session.refresh(test_user)
        await db_session.refresh(anchor)
        await db_session.refresh(companion)
        assert missing_photo.status_code == 409
        assert missing_photo.json()["detail"] == "NO_PREVIEW_PHOTO"
        assert await learning_rows() == baseline
    assert (
        await client.post(f"{PATH}/{anchor.id}/bought", headers=auth_headers)
    ).status_code == 200
    worn = await client.post(
        f"/api/v1/outfits/{outfit_id}/wear-today", headers=auth_headers, json={}
    )
    assert worn.status_code == 200, worn.text
    assert worn.json()["unowned_item_ids"] == []
    await db_session.refresh(anchor)
    await db_session.refresh(companion)
    assert anchor.wear_count == companion.wear_count == 1
    learned = await learning_rows()
    assert learned != baseline
    assert learned[0] and learned[1] and learned[2]


@pytest.mark.parametrize("state", ["bought", "passed", "unready", "archived"])
async def test_direct_preview_rejects_unavailable_anchor(
    client, auth_headers, db_session, test_user, state
):
    anchor, *_rest = await fixture_items(db_session, test_user)
    if state in ("bought", "passed"):
        purchase = await db_session.get(ItemPurchase, anchor.id)
        purchase.state = state
    elif state == "unready":
        anchor.status = ItemStatus.processing
    else:
        anchor.is_archived = True
    await db_session.commit()
    response = await client.post(f"{PATH}/{anchor.id}/outfits", headers=auth_headers, json={})
    assert response.status_code == (409 if state in ("bought", "passed") else 422)


@pytest.mark.parametrize(
    "action", ["wore", "wore-instead", "feedback", "accept", "reject", "feedback-accept"]
)
async def test_unowned_source_refuses_wear_overrides_and_acceptance(
    client, auth_headers, db_session, test_user, action
):
    from uuid import UUID

    from app.utils.timezone import get_user_today

    anchor, _, companion, *_rest = await fixture_items(db_session, test_user)
    created = await client.post(f"{PATH}/{anchor.id}/outfits", headers=auth_headers, json={})
    outfit_id = created.json()["id"]
    await db_session.commit()
    payload = {
        "wore": {"date": get_user_today(test_user).isoformat(), "item_ids": [str(companion.id)]},
        "wore-instead": {"items": [str(companion.id)]},
        "feedback": {"actually_worn": False, "wore_instead_items": [str(companion.id)]},
        "feedback-accept": {"accepted": True},
    }.get(action, {})
    suffix = "feedback" if action == "feedback-accept" else action
    response = await client.post(
        f"/api/v1/outfits/{outfit_id}/{suffix}", headers=auth_headers, json=payload
    )
    assert response.status_code == (
        403 if action in ("wore", "wore-instead", "feedback") else 422
    ), response.text
    assert (await db_session.get(Outfit, UUID(outfit_id))).status == "pending"
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Outfit).where(Outfit.user_id == test_user.id)
        )
        == 1
    )
    await db_session.refresh(companion)
    assert companion.wear_count == 0
    assert (
        await client.post(f"{PATH}/{anchor.id}/bought", headers=auth_headers)
    ).status_code == 200
    allowed = await client.post(
        f"/api/v1/outfits/{outfit_id}/{suffix}", headers=auth_headers, json=payload
    )
    assert allowed.status_code == 200, allowed.text


async def test_suggest_options_batches_purchase_queries(
    client, auth_headers, db_session, test_user
):
    from unittest.mock import AsyncMock, patch

    from sqlalchemy import event

    from app.services.studio_service import StudioService

    _, _, companion, *_rest = await fixture_items(db_session, test_user)
    outfits = [
        await StudioService(db_session).create_from_scratch(
            test_user, [companion.id], "casual", None, None, False, None
        )
        for _index in range(3)
    ]
    await db_session.commit()
    outfits = [await StudioService(db_session).get_full_outfit(outfit.id) for outfit in outfits]
    membership_queries = []

    def observe(_connection, _cursor, _statement, _parameters, context, _executemany):
        statement = context.compiled.statement if context.compiled else None
        if getattr(statement, "is_select", False) and any(
            getattr(source, "name", None) == "item_purchases"
            for source in statement.get_final_froms()
        ):
            membership_queries.append(statement)

    event.listen(db_session.bind.sync_engine, "before_cursor_execute", observe)
    try:
        counts = []
        for count in (1, 3):
            membership_queries.clear()
            with patch(
                "app.services.recommendation_service.RecommendationService.generate_recommendations",
                AsyncMock(return_value=outfits[:count]),
            ):
                response = await client.post(
                    "/api/v1/outfits/suggest-options",
                    headers=auth_headers,
                    json={"occasion": "casual"},
                )
            assert response.status_code == 200, response.text
            counts.append(len(membership_queries))
        assert counts == [1, 1]
    finally:
        event.remove(db_session.bind.sync_engine, "before_cursor_execute", observe)
