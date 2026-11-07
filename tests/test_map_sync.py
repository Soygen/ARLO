"""CV helpers for in-game map player detection / registration."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from arc_helper.map_sync import find_teal_player
from arc_helper.map_sync import identify_map_from_ocr

FIXTURES = Path(__file__).parent / "fixtures" / "map_sync"


@pytest.mark.skipif(
    not (FIXTURES / "player_arrow.png").is_file(),
    reason="player arrow fixture missing",
)
def test_find_teal_on_arrow_crop():
    im = Image.open(FIXTURES / "player_arrow.png")
    hit = find_teal_player(im)
    assert hit is not None
    (x, y), facing, score = hit
    assert score > 0
    assert 0 <= x <= im.width
    assert 0 <= y <= im.height
    assert facing is not None


@pytest.mark.skipif(
    not (FIXTURES / "blue_gate_zoomed_out.png").is_file(),
    reason="blue gate screenshot fixture missing",
)
def test_find_teal_on_blue_gate_screenshot():
    im = Image.open(FIXTURES / "blue_gate_zoomed_out.png")
    hit = find_teal_player(im)
    assert hit is not None
    (x, y), _facing, score = hit
    # Player is in lower-center Farmlands area on the sample
    assert score > 0
    assert im.width * 0.2 < x < im.width * 0.8
    assert y > im.height * 0.35


def test_identify_map_from_ocr_titles():
    assert identify_map_from_ocr("THE BLUE GATE") == "blue-gate"
    assert identify_map_from_ocr("Dam Battlegrounds 18:51") == "dam"
    assert identify_map_from_ocr("nothing useful") is None


def test_thumbs_differ_motion_threshold():
    import numpy as np

    from arc_helper.map_sync import thumbs_differ

    a = np.zeros((8, 8), dtype=np.uint8)
    b = a.copy()
    assert not thumbs_differ(a, b)
    b = a.copy()
    b[0, 0] = 255
    # Single pixel ≠ mean threshold across 64 cells
    assert not thumbs_differ(a, b, threshold=5.5)
    b[:] = 40
    assert thumbs_differ(a, b, threshold=5.5)
    assert thumbs_differ(None, a)
