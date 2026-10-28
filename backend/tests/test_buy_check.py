import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models.considering import ItemPurchase
from app.models.fit_profile import UserFitProfile
from app.models.item import ClothingItem, ItemStatus
from app.models.notification import NotificationSettings
from tests.test_considering_preview import remove_test_account as remove_test_account

pytestmark = pytest.mark.asyncio
FIXTURE = json.loads((Path(__file__).parent / "fixtures/buy_check_score.json").read_text())


@pytest.fixture
async def shelf(db_session, test_user):
    candidate = ClothingItem(
        user_id=test_user.id,
        type="shirt",
        brand="Example",
        primary_color="blue",
        status=ItemStatus.ready,
        image_path="fixture.png",
        tags={"material_detail": "100% cotton", "fit": "regular"},
        notes="Line: New | Size: M | Fit: regular | Fabric: 100% cotton",
    )
    db_session.add(candidate)
    await db_session.flush()
    db_session.add(ItemPurchase(item_id=candidate.id, planned_size="M"))
    for color, rating in [("green", "love"), ("red", "like")]:
        db_session.add(
            ClothingItem(
                user_id=test_user.id,
                type="shirt",
                brand="Example",
                primary_color=color,
                status=ItemStatus.ready,
                image_path="fixture.png",
                wear_count=40,
                tags={"material_detail": "100% cotton", "fit": "regular"},
                notes=f"Line: Old | Rating: {rating} | Fit: regular | Works: collar stays flat",
            )
        )
    db_session.add(
        NotificationSettings(
            user_id=test_user.id,
            channel="ntfy",
            config={"server": "http://capture.test", "topic": "stub-notices"},
        )
    )
    await db_session.commit()
    return candidate


@pytest.fixture
async def provider(monkeypatch, session_maker):
    from app.services import ai_telemetry_service as telemetry
    from app.services import buy_check_service as service

    settings = get_settings()
    monkeypatch.setattr(settings, "wardrowbe_openrouter_api_key", "stub-only")
    calls, records = [], []
    responses = []

    async def handle(request):
        calls.append(json.loads(request.content))
        status, body = responses.pop(0) if responses else (200, copy.deepcopy(FIXTURE))
        return httpx.Response(status, json=body)

    monkeypatch.setattr(
        service,
        "AsyncClient",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs),
    )
    monkeypatch.setattr(telemetry, "_enqueue", records.append)
    notify = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(service.NtfyProvider, "send", notify)
    return calls, records, responses, notify


async def check(client, auth_headers, shelf):
    response = await client.post(f"/api/v1/considering/{shelf.id}/buy-check", headers=auth_headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_fallback_records_raw_answers_and_throttles_notice(
    client, auth_headers, shelf, provider
):
    calls, records, responses, notify = provider
    responses.extend([(404, {"error": {"message": "No endpoints found"}}), (200, FIXTURE)] * 2)
    for _attempt in range(2):
        result = await check(client, auth_headers, shelf)
        assert result["percentage"] == 98 and result["fallback_reason"] == "http_404"
        assert result["model_used"] == FIXTURE["model"]
    assert [call["model"] for call in calls] == ["baseten/elephant-1:free", "typesafe/jev-1.14"] * 2
    assert notify.await_count == 1
    assert len(records) == 4 and records[1]["fallback_reason"] == "http_404"
    assert records[1]["answer"]["structured"][0] == FIXTURE["answers"]
    assert "stub-only" not in json.dumps(result) and "stub-only" not in str(records)


@pytest.mark.parametrize("case", ["missing_key", "both_down", "malformed"])
async def test_clear_error_without_percentage(
    client, auth_headers, shelf, provider, monkeypatch, case
):
    calls, records, responses, _notify = provider
    if case == "missing_key":
        monkeypatch.setattr(get_settings(), "wardrowbe_openrouter_api_key", None)
    else:
        responses.extend([(503, {}) if case == "both_down" else (200, {"answers": {}})] * 2)
    result = await check(client, auth_headers, shelf)
    assert result["status"] == "error" and result["percentage"] is None and result["error"]
    if case != "missing_key":
        assert len(calls) == 2 and records[1]["fallback_reason"] == (
            "http_503" if case == "both_down" else "invalid_answer"
        )
    assert "stub-only" not in json.dumps(result) and "stub-only" not in str(records)


@pytest.mark.parametrize("gate", ["skip", "wrong_size", "duplicate"])
async def test_hard_gates_replace_number(
    client, auth_headers, shelf, provider, db_session, test_user, gate
):
    if gate == "skip":
        db_session.add(
            UserFitProfile(
                user_id=test_user.id,
                profile={
                    "rev": "skip",
                    "skip_list": [{"brand": "Example", "reason": "Sleeves too short"}],
                },
            )
        )
    elif gate == "wrong_size":
        db_session.add(
            UserFitProfile(
                user_id=test_user.id,
                profile={"rev": "size", "brand_sizes": [{"brand": "Example", "size": "L"}]},
            )
        )
    else:
        db_session.add(
            ClothingItem(
                user_id=test_user.id,
                type="shirt",
                brand="Example",
                primary_color="blue",
                status=ItemStatus.ready,
                image_path="fixture.png",
                tags={"material_detail": "100% cotton"},
                notes="Line: New",
            )
        )
    await db_session.commit()
    result = await check(client, auth_headers, shelf)
    assert result["status"] == gate and result["percentage"] is None and result["minuses"]
    assert not provider[0]


async def test_cited_reasons_and_fiber_only_never_plus(
    client, auth_headers, shelf, provider, db_session, test_user
):
    result = await check(client, auth_headers, shelf)
    assert result["pluses"] and result["minuses"]
    for reason in result["pluses"] + result["minuses"] + result["unknowns"]:
        assert reason["citations"]
        assert all(
            "quote" in citation and "source_id" in citation for citation in reason["citations"]
        )
    for garment in await db_session.scalars(
        select(ClothingItem).where(
            ClothingItem.user_id == test_user.id, ClothingItem.id != shelf.id
        )
    ):
        garment.notes = "Rating: love | Fabric: 100% cotton"
        garment.tags = {"material_detail": "100% cotton"}
    await db_session.commit()
    payload = copy.deepcopy(FIXTURE)
    payload["answers"]["feel_and_quality"]["noul"] = 0.9
    provider[2].append((200, payload))
    result = await check(client, auth_headers, shelf)
    assert result["status"] == "scored"
    assert not any(reason["kind"] == "feel_and_quality" for reason in result["pluses"])
    assert any(reason["kind"] == "feel_and_quality" for reason in result["unknowns"])


async def test_foreign_user_refused(client, auth_headers, provider, db_session, shelf):
    from datetime import date

    from sqlalchemy import delete

    from app.models.item import ItemHistory
    from app.models.learning import UserLearningProfile
    from app.models.user import User

    other = User(
        id=uuid4(), external_id=str(uuid4()), email=f"{uuid4()}@example.test", display_name="Other"
    )
    other_id = other.id
    db_session.add(other)
    await db_session.flush()
    foreign_items = [
        ClothingItem(
            user_id=other.id,
            type="shirt",
            brand="Example",
            image_path="fixture.png",
            status=ItemStatus.ready,
            wear_count=40,
            notes="Rating: love | Fit: regular | Wear: private-foreign-notes",
        )
        for _index in range(2)
    ]
    db_session.add_all(foreign_items)
    await db_session.flush()
    db_session.add_all(
        [
            ItemPurchase(item_id=foreign_items[0].id),
            ItemHistory(item_id=foreign_items[1].id, worn_at=date.today()),
            UserLearningProfile(
                user_id=other.id,
                learned_color_scores={"private-foreign-color": 1},
                learned_style_scores={"private-foreign-style": 1},
            ),
        ]
    )
    await db_session.commit()
    try:
        response = await client.post(
            f"/api/v1/considering/{foreign_items[0].id}/buy-check", headers=auth_headers
        )
        assert response.status_code == 404 and not provider[0]
        assert (await check(client, auth_headers, shelf))["status"] == "scored"
        state = json.dumps(provider[0][0]["state"])
        assert "private-foreign" not in state and str(other.id) not in state
        assert all(str(item.id) not in state for item in foreign_items)
    finally:
        await db_session.rollback()
        await db_session.execute(delete(User).where(User.id == other_id))
        await db_session.commit()


@pytest.mark.parametrize("change", ["legend", "missing", "boolean", "sum", "nan", "noul"])
async def test_parser_rejects_invalid_answers(change):
    from app.services.buy_check_service import parse_answer

    payload = copy.deepcopy(FIXTURE)
    rating = payload["answers"]["predicted_rating"]
    if change == "legend":
        rating["legend"]["2"] = "love"
    if change == "missing":
        rating["probabilities"].pop("0")
    if change == "boolean":
        rating["probabilities"]["0"] = False
    if change == "sum":
        rating["probabilities"]["0"] = 0.9
    if change == "nan":
        rating["probabilities"]["0"] = float("nan")
    if change == "noul":
        payload["answers"]["fit_and_cut"]["noul"] = 1.1
    with pytest.raises(ValueError):
        parse_answer(payload)


async def test_failed_notice_releases_slot_and_records_failure(
    client, auth_headers, shelf, provider
):
    _calls, records, responses, notify = provider
    notify.side_effect = [{"success": False}, {"success": True}]
    responses.extend([(404, {}), (200, FIXTURE)] * 2)
    for _attempt in range(2):
        await check(client, auth_headers, shelf)
    assert notify.await_count == 2
    assert records[1]["answer"]["fallback_notice"] == "failed"
    assert records[3]["answer"]["fallback_notice"] == "sent"


async def test_configured_models_and_negative_content_never_fall_back(
    client, auth_headers, shelf, provider, monkeypatch
):
    monkeypatch.setattr(get_settings(), "buy_check_primary_model", "stub-primary")
    monkeypatch.setattr(get_settings(), "buy_check_fallback_model", "stub-fallback")
    payload = copy.deepcopy(FIXTURE)
    payload["answers"]["predicted_rating"]["probabilities"] = {"0": 0.9, "1": 0.1, "2": 0, "3": 0}
    provider[2].append((200, payload))
    result = await check(client, auth_headers, shelf)
    assert result["percentage"] == 0 and result["fallback_reason"] is None
    assert provider[0][0]["model"] == "stub-primary" and len(provider[0]) == 1
    assert not provider[3].called
