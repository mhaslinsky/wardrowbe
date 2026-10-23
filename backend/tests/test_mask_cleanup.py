from unittest.mock import MagicMock, patch

import pytest
from PIL import Image, ImageDraw

from app.models.item import ClothingItem
from app.services.image_service import ImageService
from app.services.mask_cleanup import NoGarmentFound, clean_cutout
from tests.test_image_undo_replace import _mock_provider
from tests.test_image_undo_replace import item_with_image as item_with_image

SHIRT = (200, 40, 40)


def _photo(size=(200, 200)) -> Image.Image:
    width, height = size
    photo = Image.new("RGB", size, (240, 240, 240))
    box = (width // 5, height // 5, width * 4 // 5, height * 4 // 5)
    ImageDraw.Draw(photo).rectangle(box, fill=SHIRT)
    return photo


def _cutout(size=(200, 200), shapes=((40, 40, 160, 160),), holes=()) -> Image.Image:
    """A provider result: transparent black outside the garment, as rembg returns it."""
    cut = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(cut)
    for box in shapes:
        draw.rectangle(box, fill=(*SHIRT, 255))
    for box in holes:
        draw.rectangle(box, fill=(0, 0, 0, 0))
    return cut


class TestCleanCutout:
    def test_fills_a_print_the_model_punched_out(self):
        result = clean_cutout(_photo(), _cutout(holes=[(90, 90, 110, 110)]))

        assert result.getpixel((100, 100)) == (*SHIRT, 255)

    def test_cleanup_is_idempotent_and_does_not_change_inputs(self):
        photo = _photo()
        cutout = _cutout(shapes=[(40, 40, 160, 160), (2, 2, 6, 6)], holes=[(90, 90, 110, 110)])
        original_bytes = photo.tobytes(), cutout.tobytes()
        cleaned = clean_cutout(photo, cutout)
        assert clean_cutout(photo, cleaned).tobytes() == cleaned.tobytes()
        assert (photo.tobytes(), cutout.tobytes()) == original_bytes

    def test_fully_transparent_result_is_rejected(self):
        with pytest.raises(NoGarmentFound):
            clean_cutout(_photo(), _cutout(shapes=[]))

    def test_keeps_a_large_opening(self):
        # A bag handle: the gap inside is a real opening, not fabric.
        cut = _cutout(shapes=[(20, 20, 180, 180)], holes=[(50, 50, 150, 150)])
        result = clean_cutout(_photo(), cut)

        assert result.getpixel((100, 100))[3] == 0

    def test_drops_loose_bits_of_background(self):
        cut = _cutout(shapes=[(40, 40, 160, 160), (2, 2, 6, 6)])
        result = clean_cutout(_photo(), cut)

        assert result.getpixel((4, 4))[3] == 0
        assert result.getpixel((100, 100))[3] == 255

    def test_keeps_both_shoes_of_a_pair(self):
        cut = _cutout(shapes=[(10, 60, 90, 140), (110, 70, 180, 140)])
        result = clean_cutout(_photo(), cut)

        assert result.getpixel((50, 100))[3] == 255
        assert result.getpixel((150, 100))[3] == 255

    def test_keeps_a_small_detached_part(self):
        # A strap or a hood cut off by the model: small next to the body, still fabric.
        cut = _cutout(shapes=[(40, 40, 160, 160), (170, 40, 179, 90)])
        result = clean_cutout(_photo(), cut)

        assert result.getpixel((174, 65))[3] == 255

    @pytest.mark.parametrize("width,length", [(2, 61), (3, 400)])
    def test_keeps_a_thin_detached_strap(self, width, length):
        cutout = _cutout(
            size=(1000, 1000),
            shapes=[(100, 100, 800, 800), (900, 100, 899 + width, 99 + length)],
        )
        result = clean_cutout(_photo((1000, 1000)), cutout)
        assert result.getpixel((900, 120))[3] == 255

    @pytest.mark.parametrize("shape", ["vertical", "diagonal", "curved"])
    def test_keeps_thin_straps_in_any_orientation(self, shape):
        cutout = _cutout(size=(1000, 1000), shapes=[(100, 100, 800, 800)])
        draw = ImageDraw.Draw(cutout)
        if shape == "vertical":
            draw.line([(900, 100), (900, 160)], fill=(*SHIRT, 255), width=3)
        elif shape == "diagonal":
            draw.line([(900, 100), (940, 140)], fill=(*SHIRT, 255), width=3)
        else:
            draw.arc((900, 100, 960, 180), 0, 270, fill=(*SHIRT, 255), width=3)
        result = clean_cutout(_photo((1000, 1000)), cutout)
        region = (850, 50, 999, 250)
        assert (
            result.getchannel("A").crop(region).tobytes()
            == cutout.getchannel("A").crop(region).tobytes()
        )

    def test_keeps_a_small_elongated_opening(self):
        cutout = _cutout(holes=[(90, 60, 100, 120)])
        result = clean_cutout(_photo(), cutout)
        assert result.getpixel((95, 90))[3] == 0

    def test_keeps_a_compact_background_colored_opening(self):
        photo = _photo()
        ImageDraw.Draw(photo).rectangle((90, 90, 110, 110), fill=(240, 240, 240))
        result = clean_cutout(photo, _cutout(holes=[(90, 90, 110, 110)]))
        assert result.getpixel((100, 100))[3] == 0

    def test_accepts_a_small_garment(self):
        # A ring in the middle of the photo covers about 1% of it.
        result = clean_cutout(_photo(), _cutout(shapes=[(90, 90, 110, 110)]))

        assert result.getpixel((100, 100))[3] == 255

    def test_an_empty_result_keeps_the_original(self):
        with pytest.raises(NoGarmentFound):
            clean_cutout(_photo(), _cutout(shapes=[(0, 0, 3, 3)]))

    def test_a_result_of_another_size_is_scaled_to_the_photo(self):
        result = clean_cutout(_photo(), _cutout(size=(100, 100), shapes=[(20, 20, 80, 80)]))

        assert result.size == (200, 200)
        assert result.getpixel((100, 100))[3] == 255
        assert result.getpixel((5, 5))[3] == 0


class TestRedoFromOriginal:
    @pytest.mark.asyncio
    async def test_second_removal_starts_from_the_original_photo(
        self, item_with_image: ClothingItem
    ):
        svc = ImageService()
        provider = _mock_provider()
        with patch("app.services.background_removal.get_provider", return_value=provider):
            svc.remove_background(item_with_image.image_path)
            svc.remove_background(item_with_image.image_path)

        # A repeat run must use the green original rather than the blue cutout.
        second_source = provider.remove.call_args_list[1].args[0]
        width, height = second_source.size
        red, green, blue = second_source.getpixel((width // 2, height // 2))
        assert green > 150 and blue < 100

    @pytest.mark.asyncio
    async def test_phone_photo_bounds_inference_and_preserves_output_size(
        self, item_with_image: ClothingItem
    ):
        service = ImageService()
        full_size = (2400, 3200)
        _photo(full_size).save(service.get_image_path(item_with_image.image_path), "JPEG")
        provider = _mock_provider()
        with patch("app.services.background_removal.get_provider", return_value=provider):
            service.remove_background(item_with_image.image_path)
        assert provider.remove.call_args.args[0].size == (768, 1024)
        with Image.open(service.get_image_path(item_with_image.image_path)) as saved:
            assert saved.size == (1800, 2400)

    @pytest.mark.asyncio
    async def test_failed_removal_leaves_no_backup(self, item_with_image: ClothingItem):
        svc = ImageService()
        provider = MagicMock()
        provider.remove.side_effect = lambda img: Image.new("RGBA", img.size, (0, 0, 0, 0))
        with (
            patch("app.services.background_removal.get_provider", return_value=provider),
            pytest.raises(NoGarmentFound),
        ):
            svc.remove_background(item_with_image.image_path)

        backup = item_with_image.image_path.rsplit(".", 1)[0] + "_orig.jpg"
        assert not svc.get_image_path(backup).exists()

    @pytest.mark.asyncio
    async def test_an_interrupted_backup_write_leaves_no_backup(
        self, item_with_image: ClothingItem
    ):
        svc = ImageService()
        with (
            patch(
                "app.services.background_removal.get_provider",
                return_value=_mock_provider(),
            ),
            patch("app.services.image_service.shutil.copy2", side_effect=OSError("disk full")),
            pytest.raises(OSError),
        ):
            svc.remove_background(item_with_image.image_path)

        backup = svc.get_image_path(item_with_image.image_path.rsplit(".", 1)[0] + "_orig.jpg")
        assert not backup.exists()
        assert not list(backup.parent.glob("*.tmp"))

    @pytest.mark.asyncio
    async def test_partial_backup_write_cannot_be_restored(self, item_with_image: ClothingItem):
        service = ImageService()
        original = service.get_image_path(item_with_image.image_path).read_bytes()

        def interrupted_copy(source, destination):
            destination.write_bytes(b"partial JPEG")
            raise OSError("disk full")

        with (
            patch(
                "app.services.background_removal.get_provider",
                return_value=_mock_provider(),
            ),
            patch("app.services.image_service.shutil.copy2", side_effect=interrupted_copy),
            pytest.raises(OSError, match="disk full"),
        ):
            service.remove_background(item_with_image.image_path)

        backup = service.get_image_path(item_with_image.image_path.rsplit(".", 1)[0] + "_orig.jpg")
        assert not backup.exists()
        assert not list(backup.parent.glob(".*.tmp"))
        assert service.get_image_path(item_with_image.image_path).read_bytes() == original

    @pytest.mark.asyncio
    async def test_a_failed_rotation_leaves_the_backup_as_it_was(
        self, item_with_image: ClothingItem
    ):
        svc = ImageService()
        svc._save_all_sizes(_photo((800, 500)), item_with_image.image_path)
        with patch("app.services.background_removal.get_provider", return_value=_mock_provider()):
            svc.remove_background(item_with_image.image_path)
        backup = svc.get_image_path(item_with_image.image_path.rsplit(".", 1)[0] + "_orig.jpg")
        before = Image.open(backup).size

        with (
            patch.object(ImageService, "_save_all_sizes", side_effect=OSError("disk full")),
            pytest.raises(OSError),
        ):
            svc.rotate_image(item_with_image.image_path, "cw")

        # The photo was not turned, so neither is its backup.
        assert Image.open(backup).size == before

    @pytest.mark.asyncio
    async def test_rotation_turns_the_backup_too(self, item_with_image: ClothingItem):
        svc = ImageService()
        svc._save_all_sizes(_photo((800, 500)), item_with_image.image_path)
        with patch("app.services.background_removal.get_provider", return_value=_mock_provider()):
            result = svc.remove_background(item_with_image.image_path)
        backup_full = svc.get_image_path(result["original_backup_path"])
        with Image.open(backup_full) as before:
            width, height = before.size

        svc.rotate_image(item_with_image.image_path, "cw")

        with Image.open(backup_full) as after:
            assert after.size == (height, width)
