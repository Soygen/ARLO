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
