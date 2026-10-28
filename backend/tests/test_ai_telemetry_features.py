import base64
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.models.ai_call import AICall
from app.models.considering import ItemPurchase
from app.models.outfit import Outfit
from app.models.preview import OutfitPreview
from app.services.ai_service import AIService
from app.services.pairing_service import PairingService
from app.services.recommendation_service import RecommendationService
from app.services.selfie_service import match_selfie
from tests.test_ai_telemetry import chat_reply
from tests.test_ai_telemetry import telemetry as telemetry
from tests.test_image_undo_replace import _jpeg_bytes, _make_item
from tests.test_outfit_preview import preview_setup as preview_setup
from tests.test_outfit_preview import upload
from tests.test_recommendation_service import _weather


async def rows(db, telemetry, user):
    await telemetry.drain_recordings()
    return list(
        await db.scalars(
            select(AICall).where(AICall.user_id == user.id).order_by(AICall.created_at)
        )
    )


@pytest.mark.parametrize("mode", ["preview", "preview_redraw", "considering_preview"])
async def test_preview_caller_keeps_exact_call_id(
    telemetry, client, auth_headers, db_session, test_user, preview_setup, mode
):
    if mode == "considering_preview":
        anchor = preview_setup["items"][0]
        db_session.add(ItemPurchase(item_id=anchor))
        outfit = await db_session.get(Outfit, preview_setup["outfit"])
        outfit.source_item_id = anchor
        await db_session.commit()
    await upload(client, auth_headers)
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"inlineData": {"data": base64.b64encode(_jpeg_bytes()).decode()}}]
                }
            }
        ]
    }
    response = httpx.Response(200, json=payload, request=httpx.Request("POST", "https://stub.test"))
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=response)):
        from app.services.preview_service import PreviewService

        await PreviewService(db_session).generate(
            preview_setup["outfit"], test_user.id, mode == "preview_redraw"
        )
    recorded = await rows(db_session, telemetry, test_user)
    assert len(recorded) == 1 and recorded[0].feature == mode
    preview = await db_session.get(OutfitPreview, preview_setup["outfit"])
    assert preview.usage["_ai_call_id"] == str(recorded[0].id)


@pytest.mark.parametrize("feature", ["outfit_suggestion", "trip_suggestion", "pairing"])
async def test_outfit_callers_keep_call_id(telemetry, db_session, test_user, feature):
    items = [await _make_item(db_session, test_user) for _ in range(3)]
    items[0].type, items[1].type, items[2].type = "shirt", "pants", "sneakers"
    await db_session.commit()
    test_user.preferences = None
    number_map = {number: item.id for number, item in enumerate(items, 1)}
    answer = {"items": [1, 2, 3], "headline": "Look", "highlights": [], "styling_tip": ""}
    response = httpx.Response(
        200,
        json=chat_reply(json.dumps([answer] if feature == "pairing" else answer)),
        request=httpx.Request("POST", "https://stub.test"),
    )
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=response)):
        if feature == "pairing":
            service = PairingService(db_session)
            with patch.object(
                service, "_format_items_for_prompt", return_value=("source", "", 1, number_map)
            ):
                outfits = await service.generate_pairings(test_user, items[0].id, 1)
        else:
            service = RecommendationService(db_session)
            service.telemetry_feature = feature
            with patch.object(service, "_format_items_for_prompt", return_value=("", number_map)):
                outfits = await service.generate_recommendations(
                    test_user, "casual", weather_override=_weather(temp=18), single_outfit=True
                )
    recorded = await rows(db_session, telemetry, test_user)
    assert len(recorded) == 1 and recorded[0].feature == feature
    assert outfits[0].ai_raw_response["_ai_call_id"] == str(recorded[0].id)


async def test_selfie_request_does_not_mutate_shared_client(
    telemetry, db_session, test_user, tmp_path
):
    item = await _make_item(db_session, test_user)
    photo = tmp_path / "selfie.jpg"
    photo.write_bytes(_jpeg_bytes())
    ai = AIService()
    response = httpx.Response(
        200, json=chat_reply('{"matches": [1]}'), request=httpx.Request("POST", "https://stub.test")
    )
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=response)):
        assert await match_selfie(ai, photo, [item]) == [item]
    recorded = await rows(db_session, telemetry, test_user)
    assert len(recorded) == 1 and recorded[0].feature == "selfie"
    assert ai.telemetry is None


async def test_tagging_worker_records_both_model_passes(
    telemetry, db_session, test_user, session_maker
):
    from app.services.image_service import ImageService
    from app.workers.tagging import tag_item_image

    item = await _make_item(db_session, test_user)
    path = ImageService().get_image_path(item.image_path)
    replies = [
        chat_reply('{"type":"shirt","primary_color":"blue","confidence":0.9}'),
        chat_reply("A shirt"),
    ]
    with patch(
        "httpx.AsyncClient.post",
        AsyncMock(
            side_effect=[
                httpx.Response(
                    200, json=payload, request=httpx.Request("POST", "https://stub.test")
                )
                for payload in replies
            ]
        ),
    ):
        result = await tag_item_image(
            {"db_session_factory": session_maker}, str(item.id), str(path)
        )
    assert result["status"] == "success"
    recorded = await rows(db_session, telemetry, test_user)
    assert len(recorded) == 2
    assert {row.feature for row in recorded} == {"item_tagging"}
    assert {row.resource_id for row in recorded} == {item.id}


async def test_health_checks_do_not_record(telemetry, db_session, test_user):
    ai = AIService(telemetry=telemetry.CallContext(test_user.id, "health"))
    response = httpx.Response(
        200, json={"data": []}, request=httpx.Request("GET", "https://stub.test")
    )
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=response)):
        await ai.check_health()
    assert await rows(db_session, telemetry, test_user) == []


async def test_inspiration_caller_is_attributed(telemetry, db_session, test_user):
    from app.services.inspiration_service import InspirationService
    from app.services.weather_service import WeatherService

    test_user.location_lat, test_user.location_lon = 40, -74
    await db_session.commit()
    answer = {
        "slots": [
            {
                "type": "shirt",
                "color": "blue",
                "fit": "regular",
                "pattern": "solid",
                "item_id": None,
                "match_strength": 0,
            }
        ],
        "allowed_item_ids": [],
    }
    response = httpx.Response(
        200, json=chat_reply(json.dumps(answer)), request=httpx.Request("POST", "https://stub.test")
    )
    with (
        patch.object(
            WeatherService, "get_current_weather", AsyncMock(return_value=_weather(temp=18))
        ),
        patch("httpx.AsyncClient.post", AsyncMock(return_value=response)),
    ):
        await InspirationService(db_session).create(test_user, _jpeg_bytes())
    recorded = await rows(db_session, telemetry, test_user)
    assert len(recorded) == 1 and recorded[0].feature == "inspiration"


async def test_photo_redraw_caller_records_assessment_and_generation(
    telemetry, db_session, test_user
):
    from app.services.photo_redraw_service import PhotoRedrawService
    from tests.test_photo_redraw import garment

    provider = PhotoRedrawService(
        telemetry=telemetry.CallContext(test_user.id, "photo_redraw", "item", test_user.id)
    )
    assessment = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {"needs_redraw": True, "confidence": 0.99, "reason": "Background"}
                            )
                        }
                    ]
                }
            }
        ]
    }
    generated = {
        "candidates": [
            {
                "content": {
                    "parts": [{"inlineData": {"data": base64.b64encode(_jpeg_bytes()).decode()}}]
                }
            }
        ]
    }
    responses = [
        httpx.Response(200, json=payload, request=httpx.Request("POST", "https://stub.test"))
        for payload in (assessment, generated)
    ]
    with (
        patch("httpx.AsyncClient.post", AsyncMock(side_effect=responses)),
        patch("app.services.photo_redraw_service.cutout", side_effect=lambda image: garment()),
    ):
        image, metadata = await provider.generate(garment().convert("RGB"))
    assert image is not None
    recorded = await rows(db_session, telemetry, test_user)
    assert [row.feature for row in recorded] == ["photo_redraw_assessment", "photo_redraw"]
