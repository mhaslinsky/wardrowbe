from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.models.item import ItemStatus
from app.services import photo_redraw_service as photos
from app.services.image_service import ImageService
from app.workers.background_removal import remove_item_background_job
from app.workers.rotation import rotate_item_image_job
from tests.test_compressed_cutouts import paths_of, persisted_cutout, snapshot


@pytest.mark.parametrize("worker", ["rotation", "background_removal"])
@pytest.mark.parametrize("failure", ["operation", "commit"])
async def test_failed_worker_preserves_committed_photos(db_session, test_user, worker, failure):
    service, item = await persisted_cutout(db_session, test_user, metadata=True)
    item_id, paths, metadata = item.id, paths_of(item), dict(item.photo_redraw)
    item.status = ItemStatus.processing
    item.processing_kind = "rotate" if worker == "rotation" else worker
    await db_session.commit()
    before = snapshot(service, test_user)
    operation = "rotate_image" if worker == "rotation" else "remove_background"
    job = rotate_item_image_job if worker == "rotation" else remove_item_background_job
    argument = "cw" if worker == "rotation" else "#ffffff"
    commit = db_session.commit

    async def fail_edit_commit():
        if item.image_path != paths["image_path"]:
            raise RuntimeError("edit commit failed")
        await commit()

    def edit(self, image_path, *args):
        self.get_image_path(image_path).write_bytes(b"edited working copy")
        if failure == "operation":
            raise OSError("edit failed")
        backup = image_path.rsplit(".", 1)[0] + "_orig.jpg"
        self.get_image_path(backup).write_bytes(b"working backup")
        return {"original_backup_path": backup}

    with (
        patch(f"app.workers.{worker}.get_db_session", return_value=db_session),
        patch.object(db_session, "close", new_callable=AsyncMock),
        patch.object(db_session, "commit", fail_edit_commit if failure == "commit" else commit),
        patch.object(ImageService, operation, edit),
    ):
        result = await job({}, str(item_id), argument)
    assert result["status"] == "error", result
    await db_session.refresh(item)
    assert paths_of(item) == paths
    assert item.photo_redraw == metadata
    assert item.status == ItemStatus.error and item.ai_started_at is None
    assert item.processing_kind == ("rotate" if worker == "rotation" else worker)
    assert snapshot(service, test_user) == before


async def test_restore_commit_failure_preserves_backup(db_session, test_user, client, auth_headers):
    service, item = await persisted_cutout(db_session, test_user)
    item_id = item.id
    backup = item.image_path.rsplit(".", 1)[0] + "_orig.jpg"
    Image.new("RGB", (300, 600), "green").save(service.get_image_path(backup))
    item.original_image_path = backup
    await db_session.commit()
    before = service.get_image_path(backup).read_bytes()
    with patch.object(db_session, "commit", side_effect=RuntimeError("commit failed")):
        response = await client.post(
            f"/api/v1/items/{item_id}/restore-original", headers=auth_headers
        )
    assert response.status_code == 500, response.text
    await db_session.rollback()
    await db_session.refresh(item)
    assert item.original_image_path == backup
    assert service.get_image_path(backup).read_bytes() == before
    response = await client.post(f"/api/v1/items/{item_id}/restore-original", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert not service.get_image_path(backup).exists()


@pytest.mark.parametrize("extension", [".jpg", ".png", ".webp"])
def test_thumbnail_write_failure_preserves_all_variants(tmp_path, monkeypatch, extension):
    service = ImageService(str(tmp_path))
    paths = {
        field: f"photo{suffix}{extension}"
        for field, suffix in zip(photos.PHOTO_FIELDS, ("", "_medium", "_thumb"), strict=True)
    }
    service._save_all_sizes(Image.new("RGB", (1000, 1000), "red"), paths["image_path"], paths)
    before = {path: service.get_image_path(path).read_bytes() for path in paths.values()}
    save = Image.Image.save

    def fail_thumbnail(image, target, *args, **kwargs):
        if image.size == (400, 400):
            if isinstance(target, (str, Path)):
                Path(target).write_bytes(b"partial image")
            else:
                assert isinstance(target, BytesIO)
                target.write(b"partial image")
            raise OSError("thumbnail write failed")
        return save(image, target, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", fail_thumbnail)
    with pytest.raises(OSError, match="thumbnail write failed"):
        service._save_all_sizes(Image.new("RGB", (1000, 1000), "blue"), paths["image_path"], paths)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("operation", ["rotate", "restore-original", "photo/redrawn"])
async def test_lost_commit_acknowledgement_keeps_owned_files(
    db_session, test_user, client, auth_headers, operation
):
    service, item = await persisted_cutout(db_session, test_user, metadata=True)
    item_id, previous_paths = item.id, paths_of(item)
    backup = item.image_path.rsplit(".", 1)[0] + "_orig.jpg"
    Image.new("RGB", (300, 600), "green").save(service.get_image_path(backup))
    item.original_image_path = backup
    await db_session.commit()
    backup_bytes = service.get_image_path(backup).read_bytes()
    commit = db_session.commit

    async def commit_then_fail():
        await commit()
        raise RuntimeError("commit acknowledgement lost")

    with patch.object(db_session, "commit", commit_then_fail):
        if operation == "photo/redrawn":
            with pytest.raises(RuntimeError, match="commit acknowledgement lost"):
                await client.post(f"/api/v1/items/{item_id}/{operation}", headers=auth_headers)
        else:
            response = await client.post(
                f"/api/v1/items/{item_id}/{operation}", headers=auth_headers
            )
            assert response.status_code == 500, response.text
    await db_session.rollback()
    await db_session.refresh(item)
    assert paths_of(item) != previous_paths
    owned = photos.retained_paths(item)
    assert all(service.get_image_path(path).exists() for path in owned.values())
    if operation == "restore-original":
        assert item.original_image_path is None
        assert backup in owned.values()
    assert service.get_image_path(backup).read_bytes() == backup_bytes


async def test_restore_cleanup_rechecks_backup_owner(
    db_session, session_maker, test_user, client, auth_headers
):
    service, item = await persisted_cutout(db_session, test_user)
    item_id = item.id
    backup = item.image_path.rsplit(".", 1)[0] + "_orig.jpg"
    Image.new("RGB", (300, 600), "green").save(service.get_image_path(backup))
    item.original_image_path = backup
    await db_session.commit()
    backup_bytes = service.get_image_path(backup).read_bytes()
    commit = db_session.commit

    async def commit_then_claim_backup():
        await commit()
        async with session_maker() as other:
            current = await photos.lock_photo(other, item_id)
            current.original_image_path = backup
            await other.commit()

    with patch.object(db_session, "commit", commit_then_claim_backup):
        response = await client.post(
            f"/api/v1/items/{item_id}/restore-original", headers=auth_headers
        )
    assert response.status_code == 200, response.text
    await db_session.refresh(item)
    assert item.original_image_path == backup
    assert service.get_image_path(backup).read_bytes() == backup_bytes
