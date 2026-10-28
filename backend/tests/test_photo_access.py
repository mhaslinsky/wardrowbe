import hashlib
import hmac
import time
from uuid import uuid4

import pytest

from app.api.auth import create_access_token
from app.config import get_settings
from app.models.family import Family
from app.models.outfit import Outfit
from app.models.preview import OutfitPreview, PreviewPhoto
from app.models.user import User
from app.services.image_service import ImageService
from tests.identity_helpers import add_users_with_identities
from tests.test_image_undo_replace import _jpeg_bytes, _make_item


@pytest.fixture
async def photos(db_session, test_user, auth_headers, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "storage_path", str(tmp_path))
    other = User(
        id=uuid4(), external_id=str(uuid4()), email=f"{uuid4()}@example.com", display_name="Other"
    )
    (identity,) = await add_users_with_identities(db_session, other)
    item = await _make_item(db_session, test_user)
    paths = {}
    for kind in ("reference", "preview", "selfie"):
        path = f"{test_user.id}/previews/{uuid4().hex}.jpg"
        target = ImageService().get_image_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_jpeg_bytes())
        paths[kind] = {"image_path": path}
    outfit = Outfit(user_id=test_user.id, occasion="casual", selfie_paths=paths["selfie"])
    inspiration = Outfit(
        user_id=test_user.id, occasion="casual", ai_raw_response={"inspiration": {"items": []}}
    )
    db_session.add_all([outfit, inspiration])
    await db_session.flush()
    db_session.add_all(
        [
            PreviewPhoto(user_id=test_user.id, paths={"front": paths["reference"]}),
            OutfitPreview(outfit_id=outfit.id, user_id=test_user.id, paths=paths["preview"]),
        ]
    )
    target = tmp_path / "inspiration" / str(test_user.id) / f"{inspiration.id}.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(_jpeg_bytes())
    family = Family(name="Test", created_by=test_user.id, invite_code=uuid4().hex[:16])
    db_session.add(family)
    await db_session.flush()
    test_user.family_id = family.id
    await db_session.commit()
    urls = {
        kind: f"/api/v1/users/me/preview-photo/files/{value['image_path']}"
        for kind, value in paths.items()
    }
    urls["item"] = f"/api/v1/images/{item.image_path}"
    urls["inspiration"] = f"/api/v1/inspiration/{inspiration.id}/image"
    keys = {kind: value["image_path"] for kind, value in paths.items()}
    keys.update(
        item=item.image_path, inspiration=f"inspiration/{test_user.id}/{inspiration.id}.jpg"
    )
    return (
        urls,
        keys,
        other,
        {"Authorization": f"Bearer {create_access_token(other.id, identity.id)}"},
    )


def legacy_link(url, key):
    expires = str(int(time.time()) + 3600)
    signing_key = hmac.new(
        get_settings().secret_key.encode(), b"image-url-signing", hashlib.sha256
    ).digest()
    signature = hmac.new(signing_key, f"{key}:{expires}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{url}?expires={expires}&sig={signature}"


@pytest.mark.parametrize("kind", ["item", "reference", "preview", "selfie", "inspiration"])
async def test_owner_bytes_are_private(client, photos, auth_headers, kind):
    urls, keys, _, _ = photos
    response = await client.get(urls[kind], headers=auth_headers)
    assert response.status_code == 200
    assert response.content == ImageService().get_image_path(keys[kind]).read_bytes()
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("kind", ["item", "reference", "preview", "selfie", "inspiration"])
@pytest.mark.parametrize("actor", ["anonymous", "other"])
@pytest.mark.parametrize("signed", [False, True])
async def test_copied_links_do_not_authorize(client, photos, kind, actor, signed):
    urls, keys, _, other_headers = photos
    url = legacy_link(urls[kind], keys[kind]) if signed else urls[kind]
    response = await client.get(url, headers=other_headers if actor == "other" else {})
    assert response.status_code == 401


@pytest.mark.parametrize("kind", ["item", "reference", "preview", "selfie", "inspiration"])
async def test_family_access_is_only_for_item_photos(client, photos, test_user, db_session, kind):
    urls, _, other, headers = photos
    other.family_id = test_user.family_id
    await db_session.commit()
    response = await client.get(urls[kind], headers=headers)
    assert response.status_code == (200 if kind == "item" else 401)
    other.family_id = None
    await db_session.commit()
    assert (await client.get(urls[kind], headers=headers)).status_code == 401


async def test_preview_path_must_still_belong_to_a_record(
    client, photos, auth_headers, db_session, test_user
):
    urls, keys, _, _ = photos
    record = await db_session.get(PreviewPhoto, test_user.id)
    record.paths = {}
    await db_session.commit()
    assert (
        await client.get(legacy_link(urls["reference"], keys["reference"]), headers=auth_headers)
    ).status_code == 404


async def test_item_response_uses_session_photo_urls(client, photos, auth_headers):
    response = await client.get("/api/v1/items", headers=auth_headers)
    assert response.status_code == 200
    for item in response.json()["items"]:
        for field in ("image_url", "medium_url", "thumbnail_url"):
            assert item[field].startswith("/photos/images/")
            assert "?" not in item[field]
