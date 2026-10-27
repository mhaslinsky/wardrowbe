from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.api.auth import create_access_token
from app.models.item import ClothingItem
from app.models.user import User
from tests.identity_helpers import add_users_with_identities
from tests.test_considering import image_upload

pytestmark = pytest.mark.asyncio
PATH = "/api/v1/considering"
HTML = b"""<main><h1>Shirt</h1><script type="application/ld+json">{"@graph":[{"@type":"Product","name":"Shirt","brand":{"name":"Maker"},"image":"/shirt.png","offers":{"price":"29.95","priceCurrency":"USD"},"size":["M","L"],"color":["Red","Blue"]}]}</script><p>Fabric: 80% cotton, 20% polyester</p><p>Machine wash cold</p><p>Relaxed fit<span hidden>Fake fabric composition</span></p><select name="size"><option value="">Choose size</option><option selected value="L">L</option></select><div id="size-chart"><table><tr><td>Chest</td><td>42 inches</td></tr></table></div></main>"""


async def test_link_extraction_preserves_fields_without_creating(client, auth_headers, db_session):
    before = await db_session.scalar(select(func.count()).select_from(ClothingItem))
    real_client = httpx.AsyncClient
    with (
        patch(
            "asyncio.BaseEventLoop.getaddrinfo",
            AsyncMock(return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        ),
        patch(
            "httpx.AsyncClient",
            side_effect=lambda **kwargs: real_client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, headers={"content-type": "text/html"}, content=HTML
                    )
                ),
                **kwargs,
            ),
        ),
    ):
        response = await client.post(
            f"{PATH}/extract", headers=auth_headers, json={"url": "https://shop.example/product"}
        )
    assert response.status_code == 200, response.text
    capture = response.json()
    assert capture["source_url"] == "https://shop.example/product"
    assert capture["name"] == "Shirt" and capture["brand"] == "Maker"
    assert capture["images"] == ["https://shop.example/shirt.png"]
    assert capture["price"] == "29.95" and capture["currency"] == "USD"
    assert capture["sizes"] == ["M", "L"] and capture["selected_size"] == "L"
    assert capture["colors"] == ["Red", "Blue"]
    assert "80% cotton" in capture["material_detail"]
    assert "Fake fabric" not in str(capture)
    assert capture["care"] == "Machine wash cold"
    assert capture["fit"] == "Relaxed fit"
    assert "42 inches" in capture["size_chart"]
    assert await db_session.scalar(select(func.count()).select_from(ClothingItem)) == before


@pytest.mark.parametrize(
    "kind,status",
    [
        ("private", 422),
        ("redirect", 422),
        ("mime", 422),
        ("oversize", 413),
        ("timeout", 422),
        ("blocked", 422),
        ("empty", 422),
        ("malformed", 422),
    ],
)
async def test_link_failures_are_explicit(client, auth_headers, kind, status):
    seen = []

    def respond(request):
        seen.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout("fixture timeout")
        if kind == "redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        return httpx.Response(
            403 if kind == "blocked" else 200,
            headers={"content-type": "image/png" if kind == "mime" else "text/html"},
            content=b"x" * (2 * 1024 * 1024 + 1)
            if kind == "oversize"
            else b'<script type="application/ld+json">{broken</script>'
            if kind == "malformed"
            else b"",
        )

    resolver = AsyncMock(
        side_effect=lambda host, *args, **kwargs: [
            (
                2,
                1,
                6,
                "",
                ("127.0.0.1" if kind == "private" or host == "127.0.0.1" else "8.8.8.8", 443),
            )
        ]
    )
    real_client = httpx.AsyncClient
    with (
        patch("asyncio.BaseEventLoop.getaddrinfo", resolver),
        patch(
            "httpx.AsyncClient",
            side_effect=lambda **kwargs: real_client(
                transport=httpx.MockTransport(respond), **kwargs
            ),
        ),
    ):
        response = await client.post(
            f"{PATH}/extract", headers=auth_headers, json={"url": "https://shop.example/product"}
        )
    assert response.status_code == status, response.text
    assert response.json()["detail"]
    if kind == "private":
        assert seen == []
    if kind == "redirect":
        assert len(seen) == 1


@pytest.mark.parametrize(
    "body", [{}, {"url": ""}, {"url": "broken"}, {"url": "file:///etc/passwd"}]
)
async def test_link_missing_and_malformed_input(client, auth_headers, body):
    assert (
        await client.post(f"{PATH}/extract", headers=auth_headers, json=body)
    ).status_code == 422


async def test_import_requires_authentication(client):
    assert (
        await client.post(f"{PATH}/extract", json={"url": "https://shop.example/product"})
    ).status_code == 401
    assert (
        await client.post(f"{PATH}/upload", files={"image": ("shirt.png", b"image", "image/png")})
    ).status_code == 401


async def test_image_import_uses_shelf_pipeline_and_isolates_users(
    client, auth_headers, db_session
):
    image = image_upload()
    response = await client.post(
        f"{PATH}/upload",
        headers=auth_headers,
        files={"image": ("shirt.png", await image.read(), "image/png")},
        data={"metadata": '{"name":"Screenshot","planned_size":"L"}'},
    )
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["state"] == "considering" and saved["source_url"] is None
    assert saved["planned_size"] == "L" and saved["item"]["name"] == "Screenshot"
    foreign = User(id=uuid4(), email=f"{uuid4()}@example.com", display_name="Foreign")
    foreign.external_id = str(foreign.id)
    (identity,) = await add_users_with_identities(db_session, foreign)
    await db_session.commit()
    foreign_headers = {"Authorization": "Bearer " + create_access_token(foreign.id, identity.id)}
    assert (
        await client.get(f"/api/v1/items/{saved['item']['id']}", headers=foreign_headers)
    ).status_code == 404
    await image.seek(0)
    duplicate = await client.post(
        f"{PATH}/upload",
        headers=auth_headers,
        files={"image": ("shirt.png", await image.read(), "image/png")},
    )
    assert duplicate.status_code == 409
    assert saved["item"]["id"] in [
        entry["item"]["id"] for entry in (await client.get(PATH, headers=auth_headers)).json()
    ]


@pytest.mark.parametrize(
    "metadata,content,mime,status",
    [
        ("{broken", b"bad", "image/png", 422),
        ("{}", b"bad", "image/png", 400),
        ("{}", b"<svg/>", "image/svg+xml", 400),
        ("{}", b"x" * (1024 * 1024 + 1), "image/png", 413),
    ],
)
async def test_image_import_rejects_invalid_without_rows(
    client, auth_headers, db_session, metadata, content, mime, status
):
    before = await db_session.scalar(select(func.count()).select_from(ClothingItem))
    from app.api.items import settings

    with patch.object(settings, "max_upload_size_mb", 1):
        response = await client.post(
            f"{PATH}/upload",
            headers=auth_headers,
            files={"image": ("image.png", content, mime)},
            data={"metadata": metadata},
        )
    assert response.status_code == status, response.text
    assert await db_session.scalar(select(func.count()).select_from(ClothingItem)) == before
