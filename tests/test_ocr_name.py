"""Tests for item-name line detection used by the two-pass OCR reader."""

from unittest.mock import MagicMock
from unittest.mock import patch

import numpy as np
from PIL import Image

from arc_helper.ocr import OCREngine


def _ocr_engine() -> OCREngine:
    settings = MagicMock()
    settings.tesseract_path = None
    settings.debug_mode = False
    settings.debug_output_dir = MagicMock()
    settings.tooltip_capture.width = 100
    settings.tooltip_capture.height = 100
    settings.tooltip_capture.offset_x = 0
    settings.tooltip_capture.offset_y = 0
    with patch("arc_helper.ocr.get_settings", return_value=settings):
        return OCREngine()


def test_preprocess_tooltip_narrow_cream_region():
    """Exclusive cream-slice bounds used to collapse a 2-col region to width 0."""
    cream = (249, 238, 223)
    arr = np.full((10, 3, 3), 20, dtype=np.uint8)
    arr[0:8, 0:2] = cream

    out = _ocr_engine().preprocess_tooltip(Image.fromarray(arr))
    assert out.width > 0 and out.height > 0


def test_upper_ratio():
    assert OCREngine._upper_ratio("RASCAL") == 1.0  # ruff: ignore[private-member-access]
    assert OCREngine._upper_ratio("Fires explosive") < 0.3  # ruff: ignore[private-member-access]
    assert OCREngine._upper_ratio("") == 0.0  # ruff: ignore[private-member-access]


def test_single_line_name():
    text = "RASCALII\nFires explosive projectiles that only"
    assert OCREngine._count_name_lines(text) == 1  # ruff: ignore[private-member-access]


def test_tolerates_miscased_numeral():
    # "II" misread as "Il" is still mostly uppercase -> counts as the name line
    text = "BETTINA Il\nHas slow fire rate and high damage"
    assert OCREngine._count_name_lines(text) == 1  # ruff: ignore[private-member-access]


def test_multiword_multiline_name():
    text = "ADVANCED ELECTRICAL\nCOMPONENTS\nA crafting material"
    assert OCREngine._count_name_lines(text) == 2  # ruff: ignore[private-member-access]


def test_zero_when_description_leads():
    text = "the quick brown fox\nmore lowercase text"
    assert OCREngine._count_name_lines(text) == 0  # ruff: ignore[private-member-access]


def test_fuzzy_match_accepts_inventory_with_ocr_errors():
    assert OCREngine._fuzzy_match("INVENTORY", "INVENTORY")
    assert OCREngine._fuzzy_match("INVNTORY", "INVENTORY")
    assert OCREngine._fuzzy_match("INVENTORV", "INVENTORY")
    assert OCREngine._fuzzy_match("xx INVENTORY yy", "INVENTORY")


def test_fuzzy_match_rejects_shared_letters():
    # These used to match via character-set overlap and kept the scanner
    # active (and the tooltip up) on ordinary desktop text.
    assert not OCREngine._fuzzy_match("EVERYTHING", "INVENTORY")
    assert not OCREngine._fuzzy_match("DIRECTORY", "INVENTORY")
    assert not OCREngine._fuzzy_match("NOT VERY", "INVENTORY")
    assert not OCREngine._fuzzy_match("", "INVENTORY")


def test_skips_short_leading_noise():
    # A stray 1-char artifact above the name is ignored
    text = "RASCALII\nFires explosive projectiles"
    assert OCREngine._count_name_lines("x\n" + text) == 1  # ruff: ignore[private-member-access]
