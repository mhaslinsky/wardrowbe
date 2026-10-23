import json
from io import BytesIO
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item import ClothingItem, ItemHistory, ItemStatus
from app.models.outfit import Outfit
from app.services.selfie_service import parse_matches


def _jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (400, 600), (90, 90, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


class TestParseMatches:
    def test_reads_json_and_ignores_numbers_outside_the_list(self):
        assert parse_matches('Sure! {"matches": [2, 9, 2, 1]}', 3) == [1, 0]

    def test_falls_back_to_a_bare_list_of_numbers(self):
        assert parse_matches("3, 1", 3) == [2, 0]
        assert parse_matches("matches: [3, 1]", 3) == [2, 0]

    def test_numbers_in_prose_are_not_matches(self):
        assert parse_matches("It is 2 pm and I see 1 shirt", 3) == []

    def test_empty_answer(self):
        assert parse_matches('{"matches": []}', 5) == []

    def test_an_empty_json_answer_is_final(self):
        assert parse_matches('Item 2 looks close, but {"matches": []}', 5) == []

    @pytest.mark.parametrize(
        "answer",
        ['{"matches": 3}', '{"matches": [true, 1.5, "x", null]}', '{"matches": {"a": 1}}'],
    )
    def test_malformed_matches_are_ignored(self, answer):
        assert parse_matches(answer, 5) == []

    def test_numeric_strings_still_count(self):
        assert parse_matches('{"matches": ["2", 3]}', 5) == [1, 2]


class TestSelfieApi:
    @pytest.mark.asyncio
    async def test_returns_the_recognised_items(
        self, client: AsyncClient, test_user, auth_headers, db_session: AsyncSession
    ):
        items = [
            ClothingItem(
                user_id=test_user.id,
                type=item_type,
                primary_color=color,
                image_path=f"{item_type}.jpg",
                status=ItemStatus.ready,
            )
            for item_type, color in (("jeans", "blue"), ("t-shirt", "white"), ("coat", "black"))
        ]
        db_session.add_all(items)
        await db_session.commit()

        async def fake_call(self, messages, task_name, **kwargs):
            # The photo itself must reach the model, next to the listing.
            image_part = messages[1]["content"][1]
            assert image_part["type"] == "image_url"
            assert image_part["image_url"]["url"] == "data:image/jpeg;base64,aGk="
            listing = messages[1]["content"][0]["text"]
            wanted = [line.split(".")[0] for line in listing.splitlines()[1:] if "coat" not in line]
            return json.dumps({"matches": [int(number) for number in wanted]}), None, None

        with (
            patch("app.services.ai_service.AIService._call_with_fallback", fake_call),
            patch("app.services.ai_service.AIService._preprocess_image", return_value="aGk="),
        ):
            response = await client.post(
                "/api/v1/selfie",
                files={"image": ("me.jpg", _jpeg(), "image/jpeg")},
                headers=auth_headers,
            )

        assert response.status_code == 200, response.json()
        data = response.json()
        assert data["considered"] == 3
        assert sorted(item["type"] for item in data["matches"]) == ["jeans", "t-shirt"]

    @pytest.mark.asyncio
    async def test_ai_failure_is_a_clear_error(
        self, client: AsyncClient, test_user, auth_headers, db_session: AsyncSession
    ):
        db_session.add(
            ClothingItem(
                user_id=test_user.id, type="jeans", image_path="j.jpg", status=ItemStatus.ready
            )
        )
        await db_session.commit()
        with (
            patch(
                "app.services.ai_service.AIService._call_with_fallback",
                AsyncMock(return_value=(None, RuntimeError("down"), None)),
            ),
            patch("app.services.ai_service.AIService._preprocess_image", return_value="aGk="),
        ):
            response = await client.post(
                "/api/v1/selfie",
                files={"image": ("me.jpg", _jpeg(), "image/jpeg")},
                headers=auth_headers,
            )
        assert response.status_code == 502
        assert (
            response.json()["detail"] == "The AI could not read the photo. Try again in a moment."
        )

    @pytest.mark.asyncio
    async def test_no_photo_is_sent_while_vision_is_off(
        self, client: AsyncClient, test_user, auth_headers
    ):
        call = AsyncMock()
        with (
            patch("app.api.selfie.get_settings") as settings,
            patch("app.services.ai_service.AIService._call_with_fallback", call),
        ):
            settings.return_value.effective_ai_vision_enabled = False
            response = await client.post(
                "/api/v1/selfie",
                files={"image": ("me.jpg", _jpeg(), "image/jpeg")},
                headers=auth_headers,
            )
        assert response.status_code == 503
        call.assert_not_called()

    @pytest.mark.asyncio
    async def test_photos_over_the_pixel_ceiling_are_refused(
        self, client: AsyncClient, test_user, auth_headers
    ):
        call = AsyncMock()
        with (
            patch("app.api.selfie.get_settings") as settings,
            patch("app.services.ai_service.AIService._call_with_fallback", call),
        ):
            settings.return_value.effective_ai_vision_enabled = True
            settings.return_value.max_upload_size_mb = 10
            settings.return_value.max_image_megapixels = 0.1
            response = await client.post(
                "/api/v1/selfie",
                files={"image": ("me.jpg", _jpeg(), "image/jpeg")},
                headers=auth_headers,
            )
        assert response.status_code == 413
        call.assert_not_called()

    @pytest.mark.asyncio
    async def test_considered_counts_only_what_the_model_saw(
        self, client: AsyncClient, test_user, auth_headers, db_session: AsyncSession
    ):
        db_session.add_all(
            ClothingItem(
                user_id=test_user.id,
                type="t-shirt",
                image_path=f"{number}.jpg",
                status=ItemStatus.ready,
            )
            for number in range(4)
        )
        await db_session.commit()
        with (
            patch("app.services.selfie_service.MAX_CANDIDATES", 2),
            patch(
                "app.services.ai_service.AIService._call_with_fallback",
                AsyncMock(return_value=('{"matches": []}', None, None)),
            ),
            patch("app.services.ai_service.AIService._preprocess_image", return_value="aGk="),
        ):
            response = await client.post(
                "/api/v1/selfie",
                files={"image": ("me.jpg", _jpeg(), "image/jpeg")},
                headers=auth_headers,
            )
        assert response.status_code == 200, response.json()
        assert response.json()["considered"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_analysis_keeps_no_photo_or_outfits(
    client, test_user, auth_headers, db_session, fail
):
    db_session.add(
        ClothingItem(
            user_id=test_user.id, type="shirt", image_path="shirt.jpg", status=ItemStatus.ready
        )
    )
    await db_session.commit()
    before_outfits = await db_session.scalar(select(func.count()).select_from(Outfit))
    before_wears = await db_session.scalar(select(func.count()).select_from(ItemHistory))
    photos = []

    async def analyse(ai, photo, items):
        assert photo.read_bytes() == _jpeg()
        photos.append(photo)
        if fail:
            raise RuntimeError("model unavailable")
        return items

    with patch("app.api.selfie.match_selfie", analyse):
        response = await client.post(
            "/api/v1/selfie",
            headers=auth_headers,
            files={"image": ("me.jpg", _jpeg(), "image/jpeg")},
        )
    assert response.status_code == (502 if fail else 200), response.text
    assert photos and all(not photo.exists() and not photo.parent.exists() for photo in photos)
    assert await db_session.scalar(select(func.count()).select_from(Outfit)) == before_outfits
    assert await db_session.scalar(select(func.count()).select_from(ItemHistory)) == before_wears


@pytest.mark.asyncio
async def test_candidates_are_only_callers_active_ready_known_items(
    client, test_user, auth_headers, db_session
):
    from app.models.user import User
    from tests.identity_helpers import add_users_with_identities

    other = User(
        id=uuid4(), email="selfie-other@example.com", name="Other", external_id=str(uuid4())
    )
    await add_users_with_identities(db_session, other)
    for owner, status, archived, item_type in [
        (test_user.id, ItemStatus.ready, False, "shirt"),
        (other.id, ItemStatus.ready, False, "shirt"),
        (test_user.id, ItemStatus.processing, False, "shirt"),
        (test_user.id, ItemStatus.ready, True, "shirt"),
        (test_user.id, ItemStatus.ready, False, "unknown"),
        (test_user.id, ItemStatus.ready, False, None),
    ]:
        db_session.add(
            ClothingItem(
                user_id=owner,
                status=status,
                is_archived=archived,
                type=item_type,
                image_path="shirt.jpg",
            )
        )
    await db_session.commit()
    with patch(
        "app.api.selfie.match_selfie", AsyncMock(side_effect=lambda ai, photo, items: items)
    ):
        response = await client.post(
            "/api/v1/selfie",
            headers=auth_headers,
            files={"image": ("me.jpg", _jpeg(), "image/jpeg")},
        )
    assert response.status_code == 200, response.text
    assert response.json()["considered"] == len(response.json()["matches"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "files, expected",
    [
        ({}, 422),
        ({"image": ("bad.jpg", b"garbage", "image/jpeg")}, 400),
        ({"image": ("bad.txt", b"garbage", "text/plain")}, 400),
    ],
)
async def test_invalid_or_missing_photo(client, test_user, auth_headers, files, expected):
    with patch("app.api.selfie.match_selfie", AsyncMock()) as analyse:
        response = await client.post("/api/v1/selfie", headers=auth_headers, files=files)
    assert response.status_code == expected
    analyse.assert_not_called()


@pytest.mark.asyncio
async def test_empty_wardrobe_skips_model(client, test_user, auth_headers):
    with patch("app.services.ai_service.AIService._call_with_fallback", AsyncMock()) as call:
        response = await client.post(
            "/api/v1/selfie",
            headers=auth_headers,
            files={"image": ("me.jpg", _jpeg(), "image/jpeg")},
        )
    assert response.status_code == 200, response.text
    assert response.json() == {"matches": [], "considered": 0}
    call.assert_not_called()


@pytest.mark.asyncio
async def test_authentication_required(client):
    response = await client.post(
        "/api/v1/selfie", files={"image": ("me.jpg", _jpeg(), "image/jpeg")}
    )
    assert response.status_code == 401
