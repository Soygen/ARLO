"""Tests for item-name line detection used by the two-pass OCR reader."""

from arc_helper.ocr import OCREngine


def test_upper_ratio():
    assert OCREngine._upper_ratio("RASCAL") == 1.0  # noqa: SLF001
    assert OCREngine._upper_ratio("Fires explosive") < 0.3  # noqa: SLF001
    assert OCREngine._upper_ratio("") == 0.0  # noqa: SLF001


def test_single_line_name():
    text = "RASCALII\nFires explosive projectiles that only"
    assert OCREngine._count_name_lines(text) == 1  # noqa: SLF001


def test_tolerates_miscased_numeral():
    # "II" misread as "Il" is still mostly uppercase -> counts as the name line
    text = "BETTINA Il\nHas slow fire rate and high damage"
    assert OCREngine._count_name_lines(text) == 1  # noqa: SLF001


def test_multiword_multiline_name():
    text = "ADVANCED ELECTRICAL\nCOMPONENTS\nA crafting material"
    assert OCREngine._count_name_lines(text) == 2  # noqa: SLF001


def test_zero_when_description_leads():
    text = "the quick brown fox\nmore lowercase text"
    assert OCREngine._count_name_lines(text) == 0  # noqa: SLF001


def test_skips_short_leading_noise():
    # A stray 1-char artifact above the name is ignored
    text = "RASCALII\nFires explosive projectiles"
    assert OCREngine._count_name_lines("x\n" + text) == 1  # noqa: SLF001
