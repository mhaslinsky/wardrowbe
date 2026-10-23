"""Tidies the cut-out a background-removal model returns before it is composited.

Models trip over clothes in two typical ways: a white print or a light patch on a
shirt is taken for background and punched out, and bits of the bed or hanger the
garment lies on survive as loose specks. Both are fixed on the mask alone, so this
works the same for every provider.
"""

import numpy as np
from PIL import Image
from scipy import ndimage

# Less opaque area than this and the model lost the garment altogether. Low enough for a
# ring or an earring photographed from a distance (0.5% of a 12 MP photo is 60,000 pixels).
MIN_COVERAGE = 0.005
# Small elongated pieces can be straps, even below the relative area cutoff.
KEEP_RATIO = 0.02
# Only compact holes with garment-colored source pixels are repaired.
MAX_HOLE_RATIO = 0.10
OPAQUE = 128
EIGHT_WAY = np.ones((3, 3), dtype=bool)


class NoGarmentFound(ValueError):
    """The model kept (almost) nothing, so its result would wipe the photo."""


def clean_cutout(original: Image.Image, cutout: Image.Image) -> Image.Image:
    """Return the cutout with specks dropped and small holes filled back in.

    A filled hole takes its colours from the original photo: providers blank the colour
    of the pixels they make transparent, and the hole must show the real fabric.
    """
    rgb = original.convert("RGB")
    cut = cutout.convert("RGBA")
    if cut.size != rgb.size:
        cut = cut.resize(rgb.size, Image.Resampling.BILINEAR)

    pixels = np.array(cut, dtype=np.uint8)
    alpha = pixels[..., 3]
    solid = alpha >= OPAQUE
    if solid.mean() < MIN_COVERAGE:
        raise NoGarmentFound("No garment found in the photo; the original was kept")

    labels, count = ndimage.label(solid, structure=EIGHT_WAY)
    keep = solid
    if count > 1:
        sizes = ndimage.sum_labels(solid, labels, index=np.arange(1, count + 1))
        elongated = np.array(
            [
                max(region[0].stop - region[0].start, region[1].stop - region[1].start)
                >= 3 * min(region[0].stop - region[0].start, region[1].stop - region[1].start)
                for region in ndimage.find_objects(labels)
            ]
        )
        kept_ids = np.flatnonzero((sizes >= sizes.max() * KEEP_RATIO) | elongated) + 1
        keep = np.isin(labels, kept_ids)
        # The soft edge around a dropped speck goes with it.
        dropped = ndimage.binary_dilation(solid & ~keep, structure=EIGHT_WAY, iterations=2)
        alpha[dropped & ~keep] = 0

    holes = ndimage.binary_fill_holes(keep) & ~keep
    if holes.any():
        hole_labels, hole_count = ndimage.label(holes)
        hole_sizes = ndimage.sum_labels(holes, hole_labels, index=np.arange(1, hole_count + 1))
        source_pixels = np.asarray(rgb, dtype=np.float32)
        garment_color = np.median(source_pixels[keep], axis=0)
        exterior = ~(keep | holes)
        background_color = np.median(source_pixels[exterior], axis=0) if exterior.any() else None
        small_ids = []
        for hole_id, region in enumerate(ndimage.find_objects(hole_labels), start=1):
            height = region[0].stop - region[0].start
            width = region[1].stop - region[1].start
            if max(height, width) >= 3 * min(height, width):
                continue
            if hole_sizes[hole_id - 1] > keep.sum() * MAX_HOLE_RATIO:
                continue
            hole_color = np.median(source_pixels[hole_labels == hole_id], axis=0)
            garment_distance = np.linalg.norm(hole_color - garment_color)
            if garment_distance > 60:
                continue
            if background_color is not None and garment_distance >= np.linalg.norm(
                hole_color - background_color
            ):
                continue
            small_ids.append(hole_id)
        filled = np.isin(hole_labels, small_ids)
        pixels[filled, :3] = np.asarray(rgb, dtype=np.uint8)[filled]
        alpha[filled] = 255

    return Image.fromarray(pixels)
