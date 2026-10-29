"""OCR-tolerant name matching used to keep the hover overlay sticky."""

from arc_helper.database import names_match


def test_exact_and_case():
    assert names_match("Wires", "Wires")
    assert names_match("Wires", "wires")
    assert names_match("ADVANCED ELECTRICAL COMPONENTS", "advanced electrical components")


def test_space_and_punctuation_noise():
    assert names_match("IL TORO II", "ILTOROII")
    assert names_match("Wires", "Wires!")
    assert names_match("Power Cell", "Power-Cell")


def test_close_ocr_typo():
    # Single-character OCR slips should still stick to the same item
    assert names_match("WIRES", "WIRE5")
    assert names_match("RASCAL II", "RASCAL Il")


def test_different_items_do_not_match():
    assert not names_match("Wires", "Power Cell")
    assert not names_match("RASCAL I", "RASCAL III")
    assert not names_match("", "Wires")
    assert not names_match("Wires", "")
