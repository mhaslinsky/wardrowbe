import json
from uuid import uuid4

import pytest

from app.models.outfit import Outfit
from app.models.preview import OutfitPreview
from app.models.user import User
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
