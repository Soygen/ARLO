"""Tests for BGRx frame cropping."""

import pytest

from arc_helper.screen.frame import crop_bgrx


def make_frame(width: int, height: int, stride: int | None = None) -> bytes:
    """Synthetic BGRx frame: pixel (x, y) has B=x%256, G=y%256, R=(x+y)%256."""
    stride = stride if stride is not None else width * 4
    rows = []
    for y in range(height):
        row = bytearray(stride)
        for x in range(width):
            row[x * 4] = x % 256
            row[x * 4 + 1] = y % 256
            row[x * 4 + 2] = (x + y) % 256
            row[x * 4 + 3] = 255
        rows.append(bytes(row))
    return b"".join(rows)


def expected_rgb(x: int, y: int) -> tuple[int, int, int]:
    return ((x + y) % 256, y % 256, x % 256)


def test_full_frame_crop():
    data = make_frame(8, 6)
    img = crop_bgrx(data, 8, 6, 32, (0, 0, 8, 6))
    assert img.size == (8, 6)
    assert img.mode == "RGB"
    assert img.getpixel((0, 0)) == expected_rgb(0, 0)
    assert img.getpixel((7, 5)) == expected_rgb(7, 5)


def test_offset_crop():
    data = make_frame(20, 10)
    img = crop_bgrx(data, 20, 10, 80, (5, 2, 15, 8))
    assert img.size == (10, 6)
    # pixel (0,0) of the crop is frame pixel (5,2)
    assert img.getpixel((0, 0)) == expected_rgb(5, 2)
    assert img.getpixel((9, 5)) == expected_rgb(14, 7)


def test_padded_stride():
    # 16 bytes of padding per row
    data = make_frame(8, 6, stride=8 * 4 + 16)
    img = crop_bgrx(data, 8, 6, 8 * 4 + 16, (1, 1, 7, 5))
    assert img.size == (6, 4)
    assert img.getpixel((0, 0)) == expected_rgb(1, 1)


def test_bbox_clamped_to_frame():
    data = make_frame(10, 10)
    img = crop_bgrx(data, 10, 10, 40, (-5, -5, 50, 50))
    assert img.size == (10, 10)


def test_bbox_fully_outside_returns_minimal_image():
    data = make_frame(10, 10)
    img = crop_bgrx(data, 10, 10, 40, (100, 100, 200, 200))
    # Clamps to the bottom-right corner pixel
    assert img.size == (1, 1)
    assert img.getpixel((0, 0)) == expected_rgb(9, 9)


def test_inverted_bbox_yields_minimal_strip_at_anchor():
    # right < left: clamping anchors at left and forces a 1-wide strip
    data = make_frame(10, 10)
    img = crop_bgrx(data, 10, 10, 40, (7, 0, 3, 10))
    assert img.size == (1, 10)
    assert img.getpixel((0, 0)) == expected_rgb(7, 0)


def test_short_buffer_raises():
    data = make_frame(10, 10)[:-50]
    with pytest.raises(ValueError, match="frame buffer too small"):
        crop_bgrx(data, 10, 10, 40, (0, 0, 10, 10))
