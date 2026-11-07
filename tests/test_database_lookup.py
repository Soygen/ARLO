"""Tests for OCR-tolerant item name lookup."""

from arc_helper.database import Database


def _db(tmp_path):
    db = Database(db_path=tmp_path / "items.db")
    db.clear()  # pytest may reuse tmp_path across runs; guarantee an empty table
    with db._get_connection() as conn:
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
                ("Looting Mk. 3 (Survivor)", "Keep"),
                ("Tactical Mk. 1", "Keep"),
                ("Combat Mk. 1", "Keep"),
                ("Camera Lens", "Keep"),
                ("Stitcher III", "Keep"),
                ("Water Pump", "Keep"),
                ("Dart Board", "Sell"),
                ("Industrial Magnet", "Keep"),
                ("Medium Ammo", "Keep"),
                ("Heavy Ammo", "Keep"),
                ("Arpeggio I", "Keep"),
                ("Chandelier Design", "Keep"),
                ("Sturdy Wardrobe Design", "Keep"),
                ("Spaceport Trench Tower Key", "Keep"),
                ("Crude Explosives", "Keep"),
                ("Deflated Football", "Sell"),
                ("Bicycle Pump", "Keep"),
                ("Deprecated Anvil Splitter", "Sell or recycle"),
                ("Amplified Fragments", "Keep"),
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
    assert _db(tmp_path).lookup("LOOTINGMK3CAUTIOUS").name == "Looting Mk. 3 (Cautious)"


def test_ocr_collapsed_tactical_and_combat_mk(tmp_path):
    assert _db(tmp_path).lookup("TACTICALMK").name == "Tactical Mk. 1"
    assert _db(tmp_path).lookup("COMBATMK").name == "Combat Mk. 1"
    # Trailing L is a common OCR misread of the tier digit 1
    assert _db(tmp_path).lookup("COMBATMKL").name == "Combat Mk. 1"


def test_ocr_dropped_leading_letters_suffix_match(tmp_path):
    assert _db(tmp_path).lookup("ERALENS").name == "Camera Lens"
    assert _db(tmp_path).lookup("CHERIII").name == "Stitcher III"
    assert _db(tmp_path).lookup("ERPUMP").name == "Water Pump"
    assert _db(tmp_path).lookup("TBOARD").name == "Dart Board"
    assert _db(tmp_path).lookup("MAMMO").name == "Medium Ammo"


def test_ocr_partial_looting_mk_and_survivor(tmp_path):
    assert _db(tmp_path).lookup("TINGMK").name == "Looting Mk. 1"
    assert _db(tmp_path).lookup("TINGMKSURVIVOR").name == "Looting Mk. 3 (Survivor)"


def test_ocr_ui_chrome_stripped_from_design_name(tmp_path):
    assert (
        _db(tmp_path).lookup("CHANDELIERDESIGN REQUIREDRESOURCES").name
        == "Chandelier Design"
    )
    assert (
        _db(tmp_path).lookup("STURDYWARDROBEDESIGN REQUIREDRESOURCES").name
        == "Sturdy Wardrobe Design"
    )


def test_ocr_industrial_magnet_leading_damage(tmp_path):
    assert _db(tmp_path).lookup("JSTRIALMAGNET").name == "Industrial Magnet"


def test_ocr_spaceport_trench_key_middle_gap(tmp_path):
    assert (
        _db(tmp_path).lookup("SPACEPORTTRENCH KEY").name == "Spaceport Trench Tower Key"
    )


def test_ocr_recycle_prefix_and_arpeggio(tmp_path):
    assert _db(tmp_path).lookup("CYCLEARPEGGIOI").name == "Arpeggio I"
    assert _db(tmp_path).lookup("RECYCLEBICYCL").name == "Bicycle Pump"
    # Scrubbed remnant too short / ambiguous — must not invent a match
    assert _db(tmp_path).lookup("RECYCLEADVA") is None


def test_ocr_suffix_with_long_dropped_prefix(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO items (name, action) VALUES (?, ?)",
            ("Frequency Modulation Box", "Keep"),
        )
        conn.commit()
    assert db.lookup("TIONBOX").name == "Frequency Modulation Box"


def test_ocr_crude_explosives_collapsed(tmp_path):
    assert _db(tmp_path).lookup("CRUDEEXPLOSIVES").name == "Crude Explosives"
    assert _db(tmp_path).lookup("FLATEDFOOTBALL").name == "Deflated Football"


def test_ocr_recycle_dialog_deprecated_anvil_with_output_tooltip(tmp_path):
    # Recycle panel title remnant + hovered Amplified Fragments tooltip
    db = _db(tmp_path)
    assert (
        db.lookup("EDEPRECATEDANVILS AMPLIFIEDFRAGMENTS").name == "Amplified Fragments"
    )
    # Title-only OCR (truncated DEPRECATED ANVIL SPLITTER)
    assert db.lookup("EDEPRECATEDANVILS").name == "Deprecated Anvil Splitter"
    assert db.lookup("AMPLIFIEDFRAGMENTS").name == "Amplified Fragments"
