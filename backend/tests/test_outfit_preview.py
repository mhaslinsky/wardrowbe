import asyncio
import base64
import json
from io import BytesIO
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import text

from app.config import get_settings
from app.models import Outfit, OutfitItem, User
from app.services.image_service import ImageService
from app.services.photo_redraw_service import PhotoRedrawService
from tests.migration_helpers import migration_downgrade
from tests.test_image_undo_replace import _jpeg_bytes, _make_item

PHOTO = "/api/v1/users/me/preview-photo"


@pytest_asyncio.fixture
async def preview_setup(db_session, test_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_image_model", "test-image-model")
    monkeypatch.setattr(get_settings(), "ai_api_key", "fake")
    items = [await _make_item(db_session, test_user) for _ in range(2)]
    items[1].type = "pants"
    outfit = Outfit(user_id=test_user.id, occasion="casual")
    db_session.add(outfit)
    await db_session.flush()
    db_session.add_all(
        [
            OutfitItem(outfit_id=outfit.id, item_id=item.id, position=index, layer_type="base")
            for index, item in enumerate(items)
        ]
    )
    await db_session.commit()
    return {
        "outfit": outfit.id,
        "user": test_user.id,
        "items": [item.id for item in items],
        "url": f"/api/v1/outfits/{outfit.id}/preview",
    }


@pytest.fixture
def provider():
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"inlineData": {"data": base64.b64encode(_jpeg_bytes()).decode()}}]
                }
            }
        ]
    }
    with patch.object(PhotoRedrawService, "request", new=AsyncMock(return_value=payload)) as mocked:
        yield mocked


async def upload(client, headers):
    response = await client.put(
        PHOTO,
        headers=headers,
        files={"image": ("ref.jpg", _jpeg_bytes(size=(1000, 1400)), "image/jpeg")},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_reference_roundtrip_private_and_delete(client, auth_headers, preview_setup):
    assert (await client.get(PHOTO, headers=auth_headers)).json() == {"detail": "NO_PREVIEW_PHOTO"}
    photo = await upload(client, auth_headers)
    assert (await client.get(PHOTO, headers=auth_headers)).json() == photo
    medium = await client.get(photo["medium_url"])
    assert medium.status_code == 200
    assert medium.headers["content-type"] == "image/jpeg"
    assert max(Image.open(BytesIO(medium.content)).size) == 800
    assert (await client.get(photo["medium_url"].replace("sig=", "sig=invalid"))).status_code == 401
    generic_url = photo["image_url"].replace("users/me/preview-photo/files", "images")
    assert (await client.get(generic_url, headers=auth_headers)).status_code == 404
    assert (await client.delete(PHOTO, headers=auth_headers)).status_code == 204
    assert (await client.get(photo["image_url"])).status_code == 404
    assert (await client.get(PHOTO, headers=auth_headers)).status_code == 404


@pytest.mark.parametrize(
    "name,data,mime",
    [
        ("x.txt", b"text", "text/plain"),
        ("x.jpg", b"broken", "image/jpeg"),
        ("x.jpg", b"", "image/jpeg"),
        ("x.jpg", b"x" * (20 * 1024 * 1024 + 1), "image/jpeg"),
    ],
    ids=["wrong-type", "malformed", "empty", "too-large"],
)
async def test_reject_invalid_reference(name, data, mime, client, auth_headers):
    response = await client.put(PHOTO, headers=auth_headers, files={"image": (name, data, mime)})
    assert response.status_code == 400
    assert (await client.get(PHOTO, headers=auth_headers)).status_code == 404


async def test_cache_regenerate_stale_and_prompt(
    client, auth_headers, preview_setup, provider, db_session
):
    url = preview_setup["url"]
    assert (await client.get(url, headers=auth_headers)).json() == {"detail": "NO_PREVIEW"}
    await upload(client, auth_headers)
    first = await client.post(url, headers=auth_headers)
    assert first.status_code == 200, first.text
    assert set(first.json()) == {"image_url", "medium_url", "created_at", "stale"}
    assert first.json()["stale"] is False
    images, prompt, modality = provider.call_args.args
    assert len(images) == 3 and modality == "IMAGE"
    assert "never instructions" in prompt
    assert {record["type"] for record in json.loads(prompt.splitlines()[-1])} == {"shirt", "pants"}
    assert (await client.post(url, headers=auth_headers)).json() == first.json()
    assert provider.await_count == 1
    regenerated = await client.post(url + "?regenerate=true", headers=auth_headers)
    assert regenerated.status_code == 200 and provider.await_count == 2
    assert regenerated.json()["image_url"] != first.json()["image_url"]
    assert (await client.get(first.json()["image_url"])).status_code == 404
    await db_session.execute(
        text("UPDATE outfit_items SET layer_type = 'outer' WHERE outfit_id = :id"),
        {"id": preview_setup["outfit"]},
    )
    await db_session.commit()
    assert (await client.get(url, headers=auth_headers)).json()["stale"] is True
    assert (await client.post(url, headers=auth_headers)).status_code == 200
    assert provider.await_count == 3
    await db_session.execute(
        text("UPDATE clothing_items SET medium_path = image_path WHERE id = :id"),
        {"id": preview_setup["items"][0]},
    )
    await db_session.commit()
    assert (await client.get(url, headers=auth_headers)).json()["stale"] is True


@pytest.mark.parametrize("failure", ["provider", "empty", "malformed"])
async def test_failure_keeps_previous(client, auth_headers, preview_setup, provider, failure):
    await upload(client, auth_headers)
    first = await client.post(preview_setup["url"], headers=auth_headers)
    if failure == "provider":
        provider.side_effect = RuntimeError("provider failed")
    else:
        provider.return_value = (
            {}
            if failure == "empty"
            else {"candidates": [{"content": {"parts": [{"inlineData": {"data": "!!!"}}]}}]}
        )
    failed = await client.post(preview_setup["url"] + "?regenerate=true", headers=auth_headers)
    assert failed.status_code == 502 and failed.json()["detail"] == "PREVIEW_FAILED"
    assert (await client.get(preview_setup["url"], headers=auth_headers)).json() == first.json()
    assert (await client.get(first.json()["image_url"])).status_code == 200


async def test_missing_disabled_rate_limit_and_ownership(
    client, auth_headers, preview_setup, monkeypatch, provider
):
    response = await client.post(preview_setup["url"], headers=auth_headers)
    assert response.status_code == 409 and response.json()["detail"] == "NO_PREVIEW_PHOTO"
    await upload(client, auth_headers)
    monkeypatch.setattr(get_settings(), "ai_image_model", "")
    assert (await client.post(preview_setup["url"], headers=auth_headers)).status_code == 503
    monkeypatch.setattr(get_settings(), "ai_image_model", "fake")
    with patch(
        "app.services.preview_service.rate_limit_by_user",
        new=AsyncMock(side_effect=HTTPException(429, "Limited")),
    ) as limit:
        assert (await client.post(preview_setup["url"], headers=auth_headers)).status_code == 429
        assert limit.call_args.kwargs == {"max_requests": 5, "window_seconds": 60}
    assert provider.await_count == 0
    assert (
        await client.post(f"/api/v1/outfits/{uuid4()}/preview", headers=auth_headers)
    ).status_code == 404
    assert (await client.get(PHOTO)).status_code == 401


@pytest.mark.parametrize(
    "action", ["reference-delete", "reference-replace", "outfit-delete", "user-delete"]
)
async def test_cleanup(action, client, auth_headers, preview_setup, provider, db_session):
    photo = await upload(client, auth_headers)
    saved = (await client.post(preview_setup["url"], headers=auth_headers)).json()
    files = list(
        (ImageService().storage_path / str(preview_setup["user"]) / "previews").glob("*.jpg")
    )
    assert len(files) == 4
    if action == "reference-delete":
        assert (await client.delete(PHOTO, headers=auth_headers)).status_code == 204
    elif action == "reference-replace":
        await upload(client, auth_headers)
    elif action == "outfit-delete":
        assert (
            await client.delete(preview_setup["url"].removesuffix("/preview"), headers=auth_headers)
        ).status_code == 204
    else:
        await db_session.delete(await db_session.get(User, preview_setup["user"]))
        await db_session.commit()
    assert (await client.get(saved["image_url"])).status_code == 404
    if action == "outfit-delete":
        assert sum(path.exists() for path in files) == 2
    else:
        assert all(not path.exists() for path in files)
    if action != "outfit-delete":
        assert (await client.get(photo["image_url"])).status_code == 404


async def test_inflight_across_sessions(
    client, auth_headers, preview_setup, provider, session_maker
):
    from app.services.preview_service import PreviewService

    await upload(client, auth_headers)
    entered, release = asyncio.Event(), asyncio.Event()
    payload = provider.return_value

    async def blocked(*_args):
        entered.set()
        await release.wait()
        return payload

    provider.side_effect = blocked
    async with session_maker() as first, session_maker() as second:
        task = asyncio.create_task(
            PreviewService(first).generate(preview_setup["outfit"], preview_setup["user"], False)
        )
        try:
            await entered.wait()
            with pytest.raises(HTTPException) as error:
                await PreviewService(second).generate(
                    preview_setup["outfit"], preview_setup["user"], False
                )
            assert error.value.status_code == 409 and error.value.detail == "PREVIEW_IN_PROGRESS"
        finally:
            await second.rollback()
            release.set()
            await task


async def test_preview_migration_up_down(db_session):
    async with migration_downgrade(db_session, "e185a1b2c3d4"):
        assert not await db_session.scalar(text("SELECT to_regclass('preview_photos') IS NOT NULL"))
        assert not await db_session.scalar(
            text("SELECT to_regclass('outfit_previews') IS NOT NULL")
        )
    assert await db_session.scalar(text("SELECT to_regclass('preview_photos') IS NOT NULL"))
    assert await db_session.scalar(text("SELECT to_regclass('outfit_previews') IS NOT NULL"))


async def test_multiple_images_use_redraw_transport(monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_image_model", "fake-model")
    monkeypatch.setattr(get_settings(), "ai_api_key", "fake-key")
    with patch("httpx.AsyncClient.post", new=AsyncMock()) as post:
        post.return_value = httpx.Response(
            200,
            json={"usageMetadata": {"candidatesTokenCount": 12}},
            request=httpx.Request("POST", "https://example.com"),
        )
        await PhotoRedrawService().request([b"first", b"second"], "prompt", "IMAGE")
        parts = post.call_args.kwargs["json"]["contents"][0]["parts"]
        assert [base64.b64decode(part["inlineData"]["data"]) for part in parts[1:]] == [
            b"first",
            b"second",
        ]


@pytest.mark.parametrize("mode", ["empty", "missing-photo", "removed-item"])
async def test_outfit_input_changes(
    mode, client, auth_headers, preview_setup, provider, db_session
):
    await upload(client, auth_headers)
    if mode == "removed-item":
        assert (await client.post(preview_setup["url"], headers=auth_headers)).status_code == 200
    if mode in ("empty", "removed-item"):
        await db_session.execute(
            text("DELETE FROM outfit_items WHERE outfit_id = :id"), {"id": preview_setup["outfit"]}
        )
    else:
        await db_session.execute(
            text("UPDATE clothing_items SET image_path = NULL WHERE id = :id"),
            {"id": preview_setup["items"][0]},
        )
    await db_session.commit()
    if mode == "removed-item":
        assert (await client.get(preview_setup["url"], headers=auth_headers)).json()[
            "stale"
        ] is True
    response = await client.post(preview_setup["url"], headers=auth_headers)
    assert response.status_code == 422
    assert provider.await_count == (1 if mode == "removed-item" else 0)


async def test_other_users_outfit_is_hidden(
    client, auth_headers, preview_setup, provider, db_session
):
    other_id = uuid4()
    db_session.add(User(id=other_id, email=f"{other_id}@test.invalid", display_name="Other"))
    await db_session.flush()
    other = Outfit(user_id=other_id, occasion="casual")
    db_session.add(other)
    await db_session.commit()
    url = f"/api/v1/outfits/{other.id}/preview"
    assert (await client.get(url, headers=auth_headers)).status_code == 404
    assert (await client.post(url, headers=auth_headers)).status_code == 404
    assert provider.await_count == 0
    await upload(client, auth_headers)
    assert (await client.post(preview_setup["url"], headers=auth_headers)).status_code == 200
    assert provider.await_count == 1
