import hashlib
import logging
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import ImageOps

from app.services.background_removal import get_provider
from app.services.image_service import ImageService

logger = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def crop_face(data: bytes, suffix: str) -> bytes | None:
    """Return a metadata-free head crop located by the person mask, or None."""
    try:
        original = ImageOps.exif_transpose(ImageService()._open_bounded(data, suffix)).convert(
            "RGB"
        )
        working = original.copy()
        working.thumbnail((800, 800))
        cutout = get_provider().remove(working)
        if cutout.mode != "RGBA" or cutout.size != working.size:
            return None
        mask = cutout.getchannel("A").point(lambda value: 255 if value >= 128 else 0)
        bounds = mask.getbbox()
        if bounds is None or mask.getextrema() != (0, 255):
            return None
        left, top, right, bottom = bounds
        height, width = bottom - top, right - left
        if height < working.height * 0.4 or width > height:
            return None
        head_height = height * 0.22
        head_bounds = mask.crop((left, top, right, int(top + head_height))).getbbox()
        if head_bounds is None:
            return None
        head_left, _, head_right, _ = head_bounds
        center = left + (head_left + head_right) / 2
        half_width = max((head_right - head_left) * 0.65, head_height * 0.45)
        scale = original.width / working.width
        box = (
            int(max(0, center - half_width) * scale),
            int(max(0, top - head_height * 0.1) * scale),
            int(min(working.width, center + half_width) * scale),
            int(min(working.height, top + head_height) * original.height / working.height),
        )
        if min(box[2] - box[0], box[3] - box[1]) < 32:
            return None
        buffer = BytesIO()
        cropped = original.crop(box)
        cropped.info.clear()
        cropped.save(buffer, "JPEG", quality=95, exif=b"")
        return buffer.getvalue()
    except Exception as error:
        logger.warning("Preview face crop unavailable: %s", type(error).__name__)
        return None


def face_reference(
    paths: dict[str, dict[str, str]],
) -> tuple[bytes | None, str | None]:
    """Read the front photo and hash decoded pixels and the metadata-free crop."""
    path = paths.get("front", {}).get("image_path")
    if not path:
        return None, None
    try:
        data = ImageService().get_image_path(path).read_bytes()
    except OSError:
        return None, None
    crop = crop_face(data, Path(path).suffix)
    try:
        image = ImageOps.exif_transpose(
            ImageService()._open_bounded(data, Path(path).suffix)
        ).convert("RGB")
    except Exception:
        return None, None
    pixels = str(image.size).encode() + image.tobytes()
    digest = hashlib.sha256(pixels + (crop or b"")).hexdigest()
    return crop, digest
