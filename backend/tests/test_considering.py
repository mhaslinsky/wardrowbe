from io import BytesIO
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from PIL import Image

from app.models.item import ClothingItem, ItemStatus
from app.services.item_service import ItemService
from app.services.pairing_service import PairingService
from app.services.recommendation_service import RecommendationService

PATH = "/api/v1/considering"
pytestmark = pytest.mark.asyncio


def image_upload():
    from fastapi import UploadFile
    from starlette.datastructures import Headers

    output = BytesIO()
    Image.new("RGB", (20, 20), "green").save(output, "PNG")
    output.seek(0)
    return UploadFile(
        output, filename="product.png", headers=Headers({"content-type": "image/png"})
    )


async def create(client, headers):
    with patch("app.api.considering.download_image", AsyncMock(return_value=image_upload())):
        response = await client.post(
            PATH,
            headers=headers,
            json={
                "image_urls": ["https://shop.example/image.png"],
                "name": "Possible shirt",
                "type": "shirt",
                "source_url": "https://shop.example/product",
                "price": "49.95",
                "currency": "USD",
                "planned_size": "XL",
            },
        )
    assert response.status_code == 201, response.text
    return response.json()


async def test_shelf_lifecycle_and_worker_completion(client, auth_headers, db_session, test_user):
    saved = await create(client, auth_headers)
    item_id = saved["item"]["id"]
    item = await db_session.get(ClothingItem, UUID(item_id))
    item.status = ItemStatus.processing
    await db_session.commit()
    from app.workers.tagging import mark_item_tagging_skipped

    with (
        patch("app.workers.tagging.get_db_session", return_value=db_session),
        patch.object(db_session, "close", AsyncMock()),
    ):
        await mark_item_tagging_skipped({}, item_id)
    await db_session.refresh(item)
    assert item.status == ItemStatus.ready
    item.tags = {"size": "XL", "material": "cotton"}
    await db_session.commit()
    listed = (await client.get(PATH, headers=auth_headers)).json()
    assert listed[0]["state"] == "considering"
    assert listed[0]["price"] == "49.95"
    assert not (await client.get("/api/v1/items", headers=auth_headers)).json()["items"]
    assert not await RecommendationService(db_session).get_candidate_items(
        test_user, None, "casual", None, []
    )
    assert await PairingService(db_session).get_source_item(test_user.id, item.id) is None
    assert await ItemService(db_session).get_ready_item_count(test_user.id) == 0
    assert (await client.get("/api/v1/analytics", headers=auth_headers)).json()["wardrobe"][
        "total_items"
    ] == 0
    wear = await client.post(f"/api/v1/items/{item_id}/wear", headers=auth_headers, json={})
    assert wear.status_code == 422
    wash = await client.post(f"/api/v1/items/{item_id}/in-the-wash", headers=auth_headers)
    assert wash.status_code == 404
    assert (
        await client.post(f"/api/v1/items/{item_id}/wash", headers=auth_headers, json={})
    ).status_code == 404
    assert await ItemService(db_session).get_item_types(test_user.id) == []
    assert await ItemService(db_session).get_color_distribution(test_user.id) == []
    from app.schemas.item import ItemFilter

    assert await ItemService(db_session).get_ids_by_filter(test_user.id, filters=ItemFilter()) == []
    from app.services.preview_service import snapshot
    from app.services.studio_service import validate_item_ownership

    assert await validate_item_ownership(db_session, test_user.id, [item.id]) == [item]
    from types import SimpleNamespace

    _, _, records = snapshot(
        SimpleNamespace(
            items=[SimpleNamespace(item=item, item_id=item.id, layer_type=None)], style_notes=None
        )
    )
    assert records[0]["id"] == str(item.id)
    edited = await client.patch(
        f"{PATH}/{item_id}", headers=auth_headers, json={"planned_size": "L", "price": None}
    )
    assert edited.status_code == 200
    assert edited.json()["planned_size"] == "L"
    assert edited.json()["price"] is None
    passed = await client.post(
        f"{PATH}/{item_id}/passed", headers=auth_headers, json={"reason": "Too narrow"}
    )
    assert passed.status_code == 200
    assert passed.json()["passed_reason"] == "Too narrow"
    assert (await client.get(PATH + "?state=passed", headers=auth_headers)).json()[0][
        "state"
    ] == "passed"
    restored = await client.post(f"{PATH}/{item_id}/restore", headers=auth_headers)
    assert restored.status_code == 200
    assert restored.json()["state"] == "considering"
    bought = await client.post(f"{PATH}/{item_id}/bought", headers=auth_headers)
    assert bought.status_code == 200
    assert bought.json()["item"]["image_path"] == saved["item"]["image_path"]
    assert bought.json()["item"]["tags"]["size"] == "XL"
    assert (await client.get(PATH, headers=auth_headers)).json() == []
    assert (await client.get("/api/v1/items", headers=auth_headers)).json()["total"] == 1
    await db_session.delete(item)
    await db_session.commit()


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("get", "", None),
        ("post", "", {"image_urls": ["https://example.com/a.png"]}),
        ("patch", "/{id}", {"planned_size": "L"}),
        ("post", "/{id}/bought", None),
        ("post", "/{id}/passed", {"reason": "Fit"}),
        ("post", "/{id}/restore", None),
    ],
)
async def test_every_shelf_route_requires_bearer(client, method, suffix, body):
    response = (
        await getattr(client, method)(PATH + suffix.format(id=uuid4()), json=body)
        if method != "get"
        else await client.get(PATH)
    )
    assert response.status_code == 401


async def test_url_guard_rejects_loopback(client, auth_headers):
    response = await client.post(
        PATH, headers=auth_headers, json={"image_urls": ["http://127.0.0.1/a.png"]}
    )
    assert response.status_code == 422


async def test_shelf_missing_and_malformed(client, auth_headers):
    for suffix in ("bought", "restore", "passed"):
        response = await client.post(
            f"{PATH}/{uuid4()}/{suffix}", headers=auth_headers, json={"reason": "Fit"}
        )
        assert response.status_code == 404
    for payload in ({}, {"image_urls": []}, {"image_urls": ["file:///tmp/a"]}):
        assert (await client.post(PATH, headers=auth_headers, json=payload)).status_code == 422
    assert (await client.get(PATH, headers=auth_headers)).status_code == 200


@pytest.mark.parametrize(
    "url,address",
    [
        ("file:///etc/passwd", "8.8.8.8"),
        ("http://shop.example/a", "10.0.0.1"),
        ("http://shop.example/a", "169.254.169.254"),
        ("http://shop.example/a", "::1"),
        ("http://shop.example/a", "127.0.0.1"),
        ("http://shop.example/a", "fc00::1"),
    ],
)
async def test_download_rejects_nonpublic_destinations(url, address):
    from fastapi import HTTPException

    from app.services.considering_service import download_image

    with (
        patch(
            "asyncio.BaseEventLoop.getaddrinfo",
            AsyncMock(return_value=[(2, 1, 6, "", (address, 80))]),
        ),
        patch("httpx.AsyncClient.send", AsyncMock()) as send,
    ):
        with pytest.raises(HTTPException) as error:
            await download_image(url)
        assert error.value.status_code == 422
        send.assert_not_called()


async def test_download_pins_public_address_and_rechecks_redirect():
    import httpx
    from fastapi import HTTPException

    from app.services.considering_service import download_image

    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://private.example/image"})

    resolve = AsyncMock(
        side_effect=[[(2, 1, 6, "", ("8.8.8.8", 443))], [(2, 1, 6, "", ("127.0.0.1", 80))]]
    )
    real_client = httpx.AsyncClient
    with (
        patch("asyncio.BaseEventLoop.getaddrinfo", resolve),
        patch(
            "app.services.considering_service.httpx.AsyncClient",
            side_effect=lambda **kwargs: real_client(
                transport=httpx.MockTransport(respond), **kwargs
            ),
        ),
    ):
        with pytest.raises(HTTPException):
            await download_image("https://shop.example/a")
    assert len(seen) == 1
    assert seen[0].url.host == "8.8.8.8"
    assert seen[0].headers["host"] == "shop.example"
    assert seen[0].extensions["sni_hostname"] == "shop.example"


@pytest.mark.parametrize(
    "kind,status", [("oversize", 413), ("mime", 422), ("timeout", 422), ("empty", 422)]
)
async def test_download_limits_and_failures(kind, status):
    import httpx
    from fastapi import HTTPException

    from app.services.considering_service import download_image

    def respond(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(
            200,
            headers={"content-type": "text/html" if kind == "mime" else "image/png"},
            content=b"x" * (1024 * 1024 + 1) if kind == "oversize" else b"",
        )

    real_client = httpx.AsyncClient
    with (
        patch(
            "asyncio.BaseEventLoop.getaddrinfo",
            AsyncMock(return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        ),
        patch(
            "app.services.considering_service.httpx.AsyncClient",
            side_effect=lambda **kwargs: real_client(
                transport=httpx.MockTransport(respond), **kwargs
            ),
        ),
        patch("app.services.considering_service.get_settings") as settings,
    ):
        settings.return_value.max_upload_size_mb = 1
        if kind == "empty":
            upload = await download_image("https://shop.example/a")
            from app.services.image_service import ImageService

            assert not ImageService().validate_image(await upload.read(), "image/png")
            await upload.close()
        else:
            with pytest.raises(HTTPException) as error:
                await download_image("https://shop.example/a")
            assert error.value.status_code == status


async def test_shelf_isolates_users_and_validates_reason(
    client, auth_headers, db_session, test_user
):
    from app.models.considering import ItemPurchase
    from app.models.user import User
    from tests.identity_helpers import add_users_with_identities

    other = User(id=uuid4(), email=f"{uuid4()}@example.com", display_name="Other")
    other.external_id = str(other.id)
    await add_users_with_identities(db_session, other)
    item = ClothingItem(
        user_id=other.id, type="shirt", image_path="other.png", status=ItemStatus.ready
    )
    db_session.add(item)
    await db_session.flush()
    db_session.add(ItemPurchase(item_id=item.id))
    await db_session.commit()
    assert (await client.get(PATH, headers=auth_headers)).json() == []
    assert (
        await client.patch(f"{PATH}/{item.id}", headers=auth_headers, json={"planned_size": "M"})
    ).status_code == 404
    for action in ("bought", "passed", "restore"):
        response = await client.post(
            f"{PATH}/{item.id}/{action}", headers=auth_headers, json={"reason": "Fit"}
        )
        assert response.status_code == 404
    assert (
        await client.post(f"{PATH}/{item.id}/passed", headers=auth_headers, json={"reason": "  "})
    ).status_code == 422


async def test_multiple_images_use_upload_pipeline(client, auth_headers):
    with patch(
        "app.api.considering.download_image",
        AsyncMock(side_effect=[image_upload(), image_upload()]),
    ):
        response = await client.post(
            PATH,
            headers=auth_headers,
            json={"image_urls": ["https://shop.example/a", "https://shop.example/b"]},
        )
    assert response.status_code == 201, response.text
    saved = response.json()
    assert len(saved["item"]["additional_images"]) == 1
    bought = await client.post(f"{PATH}/{saved['item']['id']}/bought", headers=auth_headers)
    assert bought.status_code == 200
    assert bought.json()["item"]["additional_images"] == saved["item"]["additional_images"]
