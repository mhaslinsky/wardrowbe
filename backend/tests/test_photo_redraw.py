import base64
import json
from io import BytesIO
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from PIL import Image

from app.config import get_settings
from app.services.image_service import ImageService
from tests.test_image_undo_replace import GREEN, RED, _jpeg_bytes, _make_item, _mock_provider


def garment(color=GREEN):
    image = Image.new("RGBA", (64, 64), (*color, 0))
    image.paste((*color, 255), (10, 10, 54, 54))
    return image


@pytest_asyncio.fixture
async def photo_item(db_session, test_user, monkeypatch):
    monkeypatch.setitem(get_settings().__dict__, "ai_image_model", "gemini-3.1-flash-image")
    monkeypatch.setattr(get_settings(), "ai_api_key", "test-key")
    return await _make_item(db_session, test_user)


@pytest.mark.parametrize("color,expected", [(GREEN, True), (RED, False), ((3, 50, 8), False)])
def test_color_drift(color, expected):
    from app.services.photo_redraw_service import colors_match

    assert colors_match(garment(), garment(color)) is expected


def test_empty_mask_rejected():
    from app.services.photo_redraw_service import colors_match

    with pytest.raises(ValueError, match="Empty garment"):
        colors_match(Image.new("RGBA", (5, 5)), garment())


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["generated", "color_drift", "failed", "skipped", "malformed"])
async def test_gemini_cutout_fallback_and_no_double_charge(mode, client, auth_headers, photo_item):
    from app.services.photo_redraw_service import PhotoRedrawService

    generated = BytesIO()
    garment().convert("RGB").save(generated, "JPEG")
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.headers["x-goog-api-key"] == "test-key"
        if len(calls) == 1:
            text = json.dumps(
                {"needs_redraw": mode != "skipped", "confidence": 0.95, "reason": "Hanging"}
            )
            parts = [{"text": text}]
        else:
            assert body["generationConfig"]["responseModalities"] == ["IMAGE"]
            parts = (
                []
                if mode == "malformed"
                else [
                    {
                        "inlineData": {
                            "data": base64.b64encode(generated.getvalue()).decode(),
                            "mimeType": "image/jpeg",
                        }
                    }
                ]
            )
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": parts}}],
                "usageMetadata": {"candidatesTokenCount": 1120},
            },
        )

    transport = httpx.MockTransport(respond)
    real_client = httpx.AsyncClient
    cutouts = [garment(), garment(RED if mode == "color_drift" else GREEN)]
    if mode == "failed":
        cutouts[1] = RuntimeError("rembg unavailable")
    original_path = photo_item.image_path
    original_bytes = ImageService().get_image_path(original_path).read_bytes()
    with (
        patch(
            "app.services.photo_redraw_service.httpx.AsyncClient",
            side_effect=lambda **kwargs: real_client(transport=transport, **kwargs),
        ),
        patch("app.services.photo_redraw_service.cutout", side_effect=cutouts),
    ):
        response = await client.post(
            f"/api/v1/items/{photo_item.id}/redraw-photo", headers=auth_headers
        )
        duplicate = await client.post(
            f"/api/v1/items/{photo_item.id}/redraw-photo", headers=auth_headers
        )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["photo_redraw"]["status"] == ("failed" if mode == "malformed" else mode)
    assert duplicate.status_code == 409
    assert len(calls) == (1 if mode == "skipped" else 2)
    assert ImageService().get_image_path(original_path).read_bytes() == original_bytes
    if mode != "generated":
        assert data["image_path"] == original_path
    else:
        assert data["image_path"] != original_path
    if mode in ("generated", "color_drift"):
        saved = ImageService().get_image_path(data["photo_redraw"]["redrawn"]["image_path"])
        assert Image.open(saved).format == "PNG"
        assert Image.open(saved).getchannel("A").getextrema() == (0, 255)
        assert data["photo_choices"]["original"] and data["photo_choices"]["redrawn"]
        with patch.object(PhotoRedrawService, "request", new_callable=AsyncMock) as paid:
            for choice in ("original", "redrawn"):
                switched = await client.post(
                    f"/api/v1/items/{photo_item.id}/photo/{choice}", headers=auth_headers
                )
                assert switched.status_code == 200, switched.text
                assert switched.json()["photo_redraw"]["selected"] == choice
                assert saved.exists()
            paid.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    ["rotate", "remove-background", "restore-original", "replace", "delete", "bulk-delete"],
)
async def test_retained_photos_survive_edits_and_delete_with_item(
    action, client, auth_headers, photo_item, db_session
):
    from app.services.photo_redraw_service import PHOTO_FIELDS, retained_paths, select_photo

    service = ImageService()
    original = service.copy_photo({field: getattr(photo_item, field) for field in PHOTO_FIELDS})
    redrawn = service.store_cutout(garment(), photo_item.user_id)
    photo_item.photo_redraw = {"status": "generated", "original": original, "redrawn": redrawn}
    select_photo(photo_item, "redrawn")
    await db_session.commit()
    immutable = {
        path: service.get_image_path(path).read_bytes()
        for path in [*original.values(), *redrawn.values()]
    }
    prefix = f"/api/v1/items/{photo_item.id}"
    if action == "replace":
        response = await client.put(
            prefix + "/image",
            files={"image": ("new.jpg", _jpeg_bytes(RED), "image/jpeg")},
            headers=auth_headers,
        )
    elif action in ("delete", "bulk-delete"):
        tracked = list(retained_paths(photo_item).values())
        if action == "delete":
            response = await client.delete(prefix, headers=auth_headers)
        else:
            response = await client.post(
                "/api/v1/items/bulk/delete",
                json={"item_ids": [str(photo_item.id)]},
                headers=auth_headers,
            )
            assert response.json()["deleted"] == 1
        assert response.status_code in (200, 204), response.text
        assert all(not service.get_image_path(path).exists() for path in tracked)
        return
    else:
        with patch("app.services.background_removal.get_provider", return_value=_mock_provider()):
            if action == "restore-original":
                removal = await client.post(
                    prefix + "/remove-background", json={}, headers=auth_headers
                )
                assert removal.status_code == 200, removal.text
            response = await client.post(prefix + "/" + action, json={}, headers=auth_headers)
    assert response.status_code == (400 if action == "rotate" else 200), response.text
    assert all(
        service.get_image_path(path).read_bytes() == content for path, content in immutable.items()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["rotate", "replace", "delete", "bulk-delete"])
async def test_no_redraw_keeps_base_file_behavior(
    action, client, auth_headers, db_session, test_user
):
    item = await _make_item(db_session, test_user)
    service = ImageService()
    paths = [item.image_path, item.medium_path, item.thumbnail_path]
    before = set(service.get_image_path(item.image_path).parent.iterdir())
    prefix = f"/api/v1/items/{item.id}"
    if action == "replace":
        response = await client.put(
            prefix + "/image",
            files={"image": ("new.jpg", _jpeg_bytes(RED), "image/jpeg")},
            headers=auth_headers,
        )
    elif action == "delete":
        response = await client.delete(prefix, headers=auth_headers)
    elif action == "bulk-delete":
        response = await client.post(
            "/api/v1/items/bulk/delete", json={"item_ids": [str(item.id)]}, headers=auth_headers
        )
        assert response.json()["deleted"] == 1
    else:
        response = await client.post(prefix + "/rotate", headers=auth_headers)
    assert response.status_code == (
        400 if action == "rotate" else 204 if action == "delete" else 200
    ), response.text
    after = set(service.get_image_path(item.image_path).parent.iterdir())
    if action == "rotate":
        assert before == after
        assert Image.open(service.get_image_path(paths[0])).size == (600, 600)
    else:
        assert all(not service.get_image_path(path).exists() for path in paths)
        assert len(after - before) == (3 if action == "replace" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "duplicate", "replace"])
async def test_changes_during_generation(
    operation, client, auth_headers, photo_item, db_session, test_user
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.api.items import delete_item
    from app.services.item_service import ItemService
    from app.services.photo_redraw_service import PhotoRedrawService

    item_id = photo_item.id
    original_path = photo_item.image_path
    retained = []

    async def generate(source):
        async with async_sessionmaker(db_session.bind, expire_on_commit=False)() as other:
            current = await ItemService(other).get_by_id(item_id, test_user.id)
            assert current.photo_redraw["status"] == "generating"
            retained.extend(current.photo_redraw["original"].values())
            if operation == "delete":
                await delete_item(item_id, other, test_user)
                await other.commit()
            elif operation == "duplicate":
                with pytest.raises(HTTPException) as error:
                    await PhotoRedrawService().redraw(other, current)
                assert error.value.status_code == 409
            else:
                current.image_path = ImageService().copy_photo({"image_path": original_path})[
                    "image_path"
                ]
                current.photo_redraw = {**current.photo_redraw, "retained": [original_path]}
                await other.commit()
        return garment(), {}

    with (
        patch.object(PhotoRedrawService, "generate", side_effect=generate),
        patch("app.services.photo_redraw_service.cutout", return_value=garment()),
    ):
        response = await client.post(f"/api/v1/items/{item_id}/redraw-photo", headers=auth_headers)
    assert response.status_code == (404 if operation == "delete" else 200), response.text
    if operation == "delete":
        assert all(not ImageService().get_image_path(path).exists() for path in retained)
    else:
        assert response.json()["photo_redraw"]["status"] == (
            "source_changed" if operation == "replace" else "generated"
        )


@pytest.mark.asyncio
async def test_bulk_missing_disabled_and_ownership(
    client, auth_headers, photo_item, db_session, monkeypatch
):
    from uuid import uuid4

    from app.services.photo_redraw_service import PhotoRedrawService

    monkeypatch.setattr(get_settings(), "ai_image_model", "")
    response = await client.post(
        f"/api/v1/items/{photo_item.id}/redraw-photo", headers=auth_headers
    )
    assert response.status_code == 503
    response = await client.post(
        f"/api/v1/items/{photo_item.id}/photo/invalid", headers=auth_headers
    )
    assert response.status_code == 400
    response = await client.post(
        f"/api/v1/items/{photo_item.id}/photo/redrawn", headers=auth_headers
    )
    assert response.status_code == 400
    response = await client.post(f"/api/v1/items/{uuid4()}/redraw-photo", headers=auth_headers)
    assert response.status_code == 404
    assert (await client.post(f"/api/v1/items/{photo_item.id}/redraw-photo")).status_code == 401
    with patch.object(PhotoRedrawService, "redraw", new_callable=AsyncMock) as redraw:
        photo_item.photo_redraw = {"status": "skipped"}
        await db_session.commit()
        response = await client.post(
            "/api/v1/items/bulk/redraw-photo",
            json={"item_ids": [str(photo_item.id), str(uuid4())]},
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["results"][0]["status"] == "skipped"
        assert response.json()["results"][1]["error"] == "Item not found"
        assert redraw.await_count == 1
