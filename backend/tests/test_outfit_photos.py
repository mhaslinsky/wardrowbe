import json
from uuid import uuid4

import pytest

from app.models.outfit import Outfit
from app.models.preview import OutfitPreview
from app.models.user import User
from app.services.image_service import ImageService
from tests.test_image_undo_replace import _jpeg_bytes
from tests.test_outfit_preview import preview_setup as preview_setup
from tests.test_outfit_wear_history import _items
from tests.test_selfie_confirm import confirm_client as confirm_client

pytest_plugins = ["tests.test_suggestion_cache"]


def photo(data=None):
    return {"image": ("me.jpg", data if data is not None else _jpeg_bytes(), "image/jpeg")}


def endpoint(setup):
    return f"/api/v1/outfits/{setup['outfit']}/photo"


async def test_attach_choice_reset_and_delete(client, auth_headers, preview_setup):
    url = endpoint(preview_setup)
    response = await client.put(url, headers=auth_headers, files=photo())
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["card_image_choice"] == "automatic"
    assert saved["card_image_url"] == saved["selfie_photo"]["medium_url"]
    image_url = saved["selfie_photo"]["image_url"]
    assert "sig=" in image_url
    served = await client.get(image_url)
    assert served.status_code == 200
    assert served.headers["cache-control"] == "private, no-store"
    for choice in ("preview", "photo", "automatic"):
        response = await client.patch(
            url + "-choice", headers=auth_headers, json={"choice": choice}
        )
        assert response.status_code == 200, response.text
        assert response.json()["card_image_choice"] == choice
        assert bool(response.json()["card_image_url"]) == (choice != "preview")
    assert (await client.delete(url, headers=auth_headers)).status_code == 204
    assert (await client.get(image_url)).status_code == 404
    detail = (await client.get(url.removesuffix("/photo"), headers=auth_headers)).json()
    assert detail["selfie_photo"] is None and detail["card_image_url"] is None


async def test_preview_choice_and_unchanged_default(
    client, auth_headers, preview_setup, db_session
):
    url = endpoint(preview_setup)
    record = OutfitPreview(
        outfit_id=preview_setup["outfit"], user_id=preview_setup["user"], paths={}
    )
    db_session.add(record)
    await db_session.commit()
    detail = (await client.get(url.removesuffix("/photo"), headers=auth_headers)).json()
    assert detail["card_image_choice"] == "automatic" and detail["card_image_url"] is None
    record.paths = {"image_path": f"{preview_setup['user']}/previews/{uuid4().hex}.jpg"}
    await db_session.commit()
    reply = await client.patch(url + "-choice", headers=auth_headers, json={"choice": "preview"})
    assert reply.status_code == 200, reply.text
    assert "sig=" in reply.json()["card_image_url"]
    assert (
        await client.patch(url + "-choice", headers=auth_headers, json={"choice": "invalid"})
    ).status_code == 422


async def test_replace_and_outfit_delete_remove_files(client, auth_headers, preview_setup):
    url = endpoint(preview_setup)
    first = await client.put(url, headers=auth_headers, files=photo())
    assert first.status_code == 200, first.text
    old = first.json()["selfie_photo"]["image_url"]
    second = await client.put(url, headers=auth_headers, files=photo())
    assert second.status_code == 200
    assert (await client.get(old)).status_code == 404
    current = second.json()["selfie_photo"]["image_url"]
    assert (
        await client.delete(url.removesuffix("/photo"), headers=auth_headers)
    ).status_code == 204
    assert (await client.get(current)).status_code == 404
    assert list(preview_setup["folder"].glob("*.jpg")) == []


async def test_cross_user_refused(client, auth_headers, preview_setup, db_session):
    url = endpoint(preview_setup)
    outfit = await db_session.get(Outfit, preview_setup["outfit"])
    other = User(
        display_name="Other", id=uuid4(), external_id=str(uuid4()), email=f"{uuid4()}@example.com"
    )
    db_session.add(other)
    await db_session.flush()
    outfit.user_id = other.id
    await db_session.commit()
    assert (await client.put(url, headers=auth_headers, files=photo())).status_code == 404
    assert (await client.delete(url, headers=auth_headers)).status_code == 404
    assert (
        await client.patch(url + "-choice", headers=auth_headers, json={"choice": "photo"})
    ).status_code == 404


@pytest.mark.parametrize(
    "case,expected", [("missing", 422), ("malformed", 400), ("oversized", 413)]
)
async def test_invalid_photo(client, auth_headers, preview_setup, case, expected):
    files = (
        None
        if case == "missing"
        else photo(b"bad" if case == "malformed" else b"x" * (20 * 1024 * 1024 + 1))
    )
    reply = await client.put(endpoint(preview_setup), headers=auth_headers, files=files)
    assert reply.status_code == expected, reply.text


async def test_confirm_keeps_photo_once(confirm_client, db_session, test_user):
    ids = await _items(db_session, test_user.id)
    data = {"request_id": str(uuid4()), "item_ids": json.dumps(ids[1:])}
    first = await confirm_client.post("/api/v1/selfie/confirm-photo", data=data, files=photo())
    assert first.status_code == 200, first.text
    saved = first.json()
    assert saved["selfie_photo"] and saved["card_image_url"]
    second = await confirm_client.post("/api/v1/selfie/confirm-photo", data=data, files=photo())
    assert second.status_code == 200
    assert second.json()["id"] == saved["id"]
    assert (
        second.json()["selfie_photo"]["image_url"].split("?")[0]
        == saved["selfie_photo"]["image_url"].split("?")[0]
    )
    url = f"/api/v1/outfits/{saved['id']}/photo"
    assert (await confirm_client.delete(url)).status_code == 204
    replay = await confirm_client.post("/api/v1/selfie/confirm-photo", data=data, files=photo())
    assert replay.status_code == 200
    assert replay.json()["selfie_photo"] is None


async def test_user_deletion_removes_photo(
    client, auth_headers, preview_setup, db_session, test_user
):
    reply = await client.put(endpoint(preview_setup), headers=auth_headers, files=photo())
    assert reply.status_code == 200
    url = reply.json()["selfie_photo"]["image_url"]
    await db_session.delete(test_user)
    await db_session.commit()
    assert (await client.get(url)).status_code == 404
    assert list(preview_setup["folder"].glob("*.jpg")) == []


async def test_failed_commit_removes_new_files(db_session, preview_setup, monkeypatch):
    from unittest.mock import AsyncMock

    from app.services.outfit_photo_service import OutfitPhotoService

    outfit = await db_session.get(Outfit, preview_setup["outfit"])
    monkeypatch.setattr(db_session, "commit", AsyncMock(side_effect=RuntimeError("commit failed")))
    with pytest.raises(RuntimeError, match="commit failed"):
        await OutfitPhotoService(db_session).save(outfit, _jpeg_bytes(), "me.jpg")
    assert list(preview_setup["folder"].glob("*.jpg")) == []


@pytest.fixture
async def family_photo(client, auth_headers, preview_setup, db_session, test_user):
    from datetime import date

    from app.api.auth import create_access_token
    from app.models.family import Family
    from app.services.preview_service import store_photo
    from tests.identity_helpers import add_users_with_identities

    other = User(
        id=uuid4(),
        external_id=str(uuid4()),
        email=f"{uuid4()}@example.com",
        display_name="Family viewer",
    )
    identities = await add_users_with_identities(db_session, other)
    family = Family(name="Photo privacy", created_by=test_user.id, invite_code=uuid4().hex[:20])
    db_session.add(family)
    await db_session.flush()
    test_user.family_id = other.family_id = family.id
    outfit = await db_session.get(Outfit, preview_setup["outfit"])
    outfit.scheduled_for = date(2026, 10, 27)
    outfit.ai_raw_response = {"inspiration": {"items": []}}
    db_session.add(
        OutfitPreview(
            outfit_id=outfit.id,
            user_id=test_user.id,
            paths=store_photo(test_user.id, _jpeg_bytes(), "preview.jpg"),
        )
    )
    await db_session.commit()
    saved = await client.put(endpoint(preview_setup), headers=auth_headers, files=photo())
    assert saved.status_code == 200
    return {
        "headers": {"Authorization": "Bearer " + create_access_token(other.id, identities[0].id)},
        "owner": str(test_user.id),
        "outfit": str(outfit.id),
        "item": str(preview_setup["items"][0]),
    }


@pytest.mark.parametrize("choice", ["automatic", "photo", "preview"])
async def test_family_feed_never_mints_private_urls(
    client, auth_headers, preview_setup, family_photo, choice
):
    response = await client.patch(
        endpoint(preview_setup) + "-choice", headers=auth_headers, json={"choice": choice}
    )
    assert response.status_code == 200
    response = await client.get(
        "/api/v1/outfits",
        params={"family_member_id": family_photo["owner"]},
        headers=family_photo["headers"],
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["outfits"]) == 1
    outfit = response.json()["outfits"][0]
    assert outfit["selfie_photo"] is None and outfit["card_image_url"] is None
    assert "/previews/" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "detail",
        "history",
        "ratings",
        "inspiration",
        "pairings",
        "considering-preview",
        "considering-suggestions",
    ],
)
async def test_non_owner_serializer_paths_have_no_private_urls(client, family_photo, path):
    headers = family_photo["headers"]
    outfit_id = family_photo["outfit"]
    if path == "detail":
        response = await client.get("/api/v1/outfits/" + outfit_id, headers=headers)
        assert response.status_code == 404
    elif path == "history":
        response = await client.get(
            "/api/v1/outfits",
            headers=headers,
            params={
                "family_member_id": family_photo["owner"],
                "date_from": "2026-10-27",
                "date_to": "2026-10-27",
            },
        )
        assert response.status_code == 200
        assert len(response.json()["outfits"]) == 1
        assert response.json()["outfits"][0]["selfie_photo"] is None
    elif path == "ratings":
        response = await client.get(
            "/api/v1/outfits/" + outfit_id + "/family-ratings", headers=headers
        )
        assert response.status_code == 200
    elif path in ("considering-preview", "considering-suggestions"):
        action = "outfits" if path == "considering-preview" else "suggestions"
        body = {"companion_ids": []} if action == "outfits" else {"occasion": "casual"}
        response = await client.post(
            "/api/v1/considering/" + family_photo["item"] + "/" + action, headers=headers, json=body
        )
        assert response.status_code in (400, 404), response.text
    else:
        response = await client.get("/api/v1/" + path, headers=headers)
        assert response.status_code == 200
    assert "/previews/" not in response.text


@pytest.mark.parametrize("existing", [False, True], ids=["attach", "replace"])
async def test_inspiration_delete_refreshes_concurrent_photo(
    client, auth_headers, preview_setup, db_session, session_maker, existing
):
    from app.services.inspiration_service import InspirationService
    from app.services.outfit_photo_service import OutfitPhotoService

    if existing:
        assert (
            await client.put(endpoint(preview_setup), headers=auth_headers, files=photo())
        ).status_code == 200
    stale = await db_session.get(Outfit, preview_setup["outfit"])
    stale.ai_raw_response = {"inspiration": {"items": []}}
    await db_session.commit()
    assert await InspirationService(db_session).find(preview_setup["user"], stale.id) is stale
    await db_session.commit()
    async with session_maker() as writer:
        service = OutfitPhotoService(writer)
        current = await service.owned(stale.id, preview_setup["user"])
        await service.save(current, _jpeg_bytes(), "new.jpg")
        newest = dict(current.selfie_paths)
    response = await client.delete("/api/v1/inspiration/" + str(stale.id), headers=auth_headers)
    assert response.status_code == 204, response.text
    assert all(not ImageService().get_image_path(path).exists() for path in newest.values())
    assert list(preview_setup["folder"].glob("*.jpg")) == []


async def test_photo_remove_refreshes_cached_row(db_session, preview_setup, session_maker):
    from app.services.outfit_photo_service import OutfitPhotoService

    stale = await db_session.get(Outfit, preview_setup["outfit"])
    await db_session.commit()
    async with session_maker() as writer:
        current = await OutfitPhotoService(writer).owned(stale.id, preview_setup["user"])
        await OutfitPhotoService(writer).save(current, _jpeg_bytes(), "new.jpg")
    service = OutfitPhotoService(db_session)
    assert await service.owned(stale.id, preview_setup["user"]) is stale
    service.clear(stale)
    await db_session.commit()
    assert list(preview_setup["folder"].glob("*.jpg")) == []


async def test_populated_downgrade_removes_files_and_restores_defaults(
    client, auth_headers, preview_setup, db_session
):
    from sqlalchemy import text

    from tests.migration_helpers import migration_downgrade

    response = await client.put(endpoint(preview_setup), headers=auth_headers, files=photo())
    assert response.status_code == 200
    old_id = uuid4()
    async with migration_downgrade(db_session, "e248a1b2c3d4"):
        assert list(preview_setup["folder"].glob("*.jpg")) == []
        await db_session.execute(
            text(
                "INSERT INTO outfits (id,user_id,occasion,source,status) VALUES (:id,:user,'casual','manual','pending')"
            ),
            {"id": old_id, "user": preview_setup["user"]},
        )
        await db_session.commit()
    row = (
        await db_session.execute(
            text("SELECT card_image_choice,selfie_paths FROM outfits WHERE id=:id"), {"id": old_id}
        )
    ).one()
    assert tuple(row) == ("automatic", None)


async def test_attach_preserves_preview_choice_response(
    client, auth_headers, preview_setup, db_session
):
    from app.services.preview_service import store_photo

    db_session.add(
        OutfitPreview(
            outfit_id=preview_setup["outfit"],
            user_id=preview_setup["user"],
            paths=store_photo(preview_setup["user"], _jpeg_bytes(), "preview.jpg"),
        )
    )
    await db_session.commit()
    choice = await client.patch(
        endpoint(preview_setup) + "-choice", headers=auth_headers, json={"choice": "preview"}
    )
    response = await client.put(endpoint(preview_setup), headers=auth_headers, files=photo())
    assert response.status_code == 200
    assert (
        response.json()["card_image_url"].split("?")[0]
        == choice.json()["card_image_url"].split("?")[0]
    )


async def test_replayed_confirm_preserves_preview_choice(confirm_client, db_session, test_user):
    from uuid import UUID

    from app.services.preview_service import store_photo

    ids = await _items(db_session, test_user.id)
    data = {"request_id": str(uuid4()), "item_ids": json.dumps(ids[1:])}
    first = await confirm_client.post("/api/v1/selfie/confirm-photo", data=data, files=photo())
    outfit_id = first.json()["id"]
    db_session.add(
        OutfitPreview(
            outfit_id=UUID(outfit_id),
            user_id=test_user.id,
            paths=store_photo(test_user.id, _jpeg_bytes(), "preview.jpg"),
        )
    )
    await db_session.commit()
    choice = await confirm_client.patch(
        "/api/v1/outfits/" + outfit_id + "/photo-choice", json={"choice": "preview"}
    )
    for route, kwargs in [
        ("confirm-photo", {"data": data, "files": photo()}),
        ("confirm", {"json": {"request_id": data["request_id"], "item_ids": ids[1:]}}),
    ]:
        replay = await confirm_client.post("/api/v1/selfie/" + route, **kwargs)
        assert replay.status_code == 200
        assert (
            replay.json()["card_image_url"].split("?")[0]
            == choice.json()["card_image_url"].split("?")[0]
        )


async def test_cancelled_save_preserves_cancellation_when_storage_fails(monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services.outfit_photo_service import OutfitPhotoService

    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def failing_store(*args):
        loop.call_soon_threadsafe(started.set)
        release.wait()
        raise OSError("Storage unavailable")

    monkeypatch.setattr("app.services.outfit_photo_service.store_photo", failing_store)
    service = OutfitPhotoService(SimpleNamespace(rollback=AsyncMock()))
    task = asyncio.create_task(service.save(SimpleNamespace(user_id=uuid4()), b"photo", "me.jpg"))
    await started.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
