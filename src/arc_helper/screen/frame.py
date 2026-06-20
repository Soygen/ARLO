"""Cropping raw BGRx frames into PIL images."""

import numpy as np
from PIL import Image


def crop_bgrx(
    data: bytes,
    width: int,
    height: int,
    stride: int,
    bbox: tuple[int, int, int, int],
) -> Image.Image:
    """Crop (left, top, right, bottom) out of a BGRx frame, returning RGB.

    Coordinates are clamped to the frame; the result is always >= 1x1.
    An inverted bbox (right <= left or bottom <= top) collapses to a
    1-pixel-wide/tall strip anchored at (left, top).

    Args:
        data: raw BGRx frame bytes.
        width: frame width in pixels.
        height: frame height in pixels.
        stride: row pitch in bytes (may exceed width*4 due to alignment padding).
        bbox: (left, top, right, bottom) crop region in pixel coordinates.
    """
    needed = height * stride
    if len(data) < needed:
        msg = f"frame buffer too small: {len(data)} < {needed} ({width}x{height}, stride {stride})"
        raise ValueError(msg)

    left, top, right, bottom = (int(v) for v in bbox)
    left = max(0, min(left, width - 1))
    top = max(0, min(top, height - 1))
    right = max(left + 1, min(right, width))
    bottom = max(top + 1, min(bottom, height))

    arr = np.frombuffer(data, dtype=np.uint8)[:needed]
    rows = arr.reshape(height, stride)
    pixels = rows[:, : width * 4].reshape(height, width, 4)
    crop = pixels[top:bottom, left:right, :3][:, :, ::-1]  # BGRx -> RGB
    return Image.fromarray(np.ascontiguousarray(crop), "RGB")
