"""Tests for OCR-tolerant item name lookup."""

from arc_helper.database import Database


def _db(tmp_path):
    db = Database(db_path=tmp_path / "items.db")
    db.clear()  # pytest may reuse tmp_path across runs; guarantee an empty table
    with db._get_connection() as conn:  # noqa: SLF001 - test seam
        conn.executemany(
            "INSERT INTO items (name, action) VALUES (?, ?)",
            [
                ("Il Toro II", "Sell"),
                ("Anvil IV", "Recycle"),
                ("Rascal I", "Sell"),
                ("Rascal II", "Keep"),
                ("Lance's Mixtape (5th edition)", "Keep"),
                ("Looting Mk. 1", "Keep"),
                ("Looting Mk. 2", "Keep"),
                ("Looting Mk. 3 (Cautious)", "Keep"),
                ("Tactical Mk. 1", "Keep"),
                ("Combat Mk. 1", "Keep"),
            ],
        )
        conn.commit()
    return db


def test_exact_match(tmp_path):
    assert _db(tmp_path).lookup("Il Toro II").name == "Il Toro II"


def test_case_insensitive(tmp_path):
    assert _db(tmp_path).lookup("ANVIL IV").name == "Anvil IV"


def test_dropped_spaces(tmp_path):
    # OCR ran the words together
    assert _db(tmp_path).lookup("ILTOROII").name == "Il Toro II"


def test_stray_trailing_punctuation(tmp_path):
    assert _db(tmp_path).lookup("ANVIL IV)").name == "Anvil IV"


def test_distinct_tiers_stay_distinct(tmp_path):
    # The numeral must not be normalized away - I and II are different items
    assert _db(tmp_path).lookup("RASCAL I").name == "Rascal I"
    assert _db(tmp_path).lookup("RASCAL II").name == "Rascal II"


def test_unknown_returns_none(tmp_path):
    assert _db(tmp_path).lookup("NONEXISTENT WIDGET") is None


def test_ocr_digit_letter_swap_mixtape(tmp_path):
    # OCR: dropped apostrophe/spaces and read 5TH as STH
    assert (
        _db(tmp_path).lookup("LANCESMIXTAPESTH EDITION").name
        == "Lance's Mixtape (5th edition)"
    )


def test_ocr_collapsed_looting_mk_defaults_to_shortest_tier(tmp_path):
    # OCR drops spaces and the tier digit → match shortest Looting Mk.
    assert _db(tmp_path).lookup("LOOTINGMK").name == "Looting Mk. 1"


def test_ocr_looting_mk_with_tier(tmp_path):
    assert _db(tmp_path).lookup("LOOTINGMK2").name == "Looting Mk. 2"
    assert (
        _db(tmp_path).lookup("LOOTINGMK3CAUTIOUS").name
        == "Looting Mk. 3 (Cautious)"
    )


def test_ocr_collapsed_tactical_and_combat_mk(tmp_path):
    assert _db(tmp_path).lookup("TACTICALMK").name == "Tactical Mk. 1"
    assert _db(tmp_path).lookup("COMBATMK").name == "Combat Mk. 1"
    # Trailing L is a common OCR misread of the tier digit 1
    assert _db(tmp_path).lookup("COMBATMKL").name == "Combat Mk. 1"
