"""Tests for keep_for formatting and merge behavior in update_db."""

from update_db import GameItem
from update_db import _workshop_rows_from_wikitext
from update_db import apply_workshop_keep_map
from update_db import build_csv_rows
from update_db import format_keep_for
from update_db import merge_action
from update_db import merge_keep_for
from update_db import outpost_room_expansion_keep_map


def test_format_keep_for_wiki_only():
    assert format_keep_for("3x for Gunsmith II Upgrade", []) == (
        "3x for Gunsmith II Upgrade"
    )


def test_format_keep_for_adds_projects_colon():
    assert format_keep_for("Projects Trophy Display (10x)", []) == (
        "Projects: Trophy Display (10x)"
    )


def test_format_keep_for_unescapes_html_entities_and_dedupes_projects():
    assert format_keep_for(
        "Projects: Soul&#39;s Offering (15x)",
        project_uses=["Soul's Offering (15x)"],
    ) == "Projects: Soul's Offering (15x)"


def test_format_keep_for_quests_only():
    assert format_keep_for("", ["The League (1x)", "Movie Night (1x)"]) == (
        "Quests: The League (1x), Movie Night (1x)"
    )


def test_format_keep_for_combines_wiki_and_quests():
    assert format_keep_for(
        "Workshop Research Station 3 (6x)",
        ["Bait Fetch Blast (1x)"],
    ) == (
        "Workshop: Research Station 3 (6x); Quests: Bait Fetch Blast (1x)"
    )


def test_format_keep_for_skips_quest_already_in_wiki():
    assert format_keep_for(
        "Quests The League (1x)",
        ["The League (1x)"],
    ) == "Quests: The League (1x)"


def test_format_keep_for_merges_projects_into_one_section():
    assert format_keep_for(
        "Projects: Expedition 1 (5x) Expedition 2 (4x)",
        project_uses=["Converging Paths (6x)", "Flickering Flames (3x)"],
    ) == (
        "Projects: Expedition 1 (5x), Expedition 2 (4x), "
        "Converging Paths (6x), Flickering Flames (3x)"
    )


def test_workshop_itemgrid_prefers_price_over_buggy_qty():
    """ItemGrid priceN wins when qty= was copied from a sibling material."""
    wikitext = """
|name1={{#replace: Apricot|{{Workshop upgrade|Scrappy|level=5|Apricot|qty=12}}}}
|price1=×12
|name2={{#replace: Mushroom|{{Workshop upgrade|Scrappy|level=5|Mushroom|qty=12}}}}
|price2=×12
|name3={{#replace: Very Comfortable Pillow|{{Workshop upgrade|Scrappy|level=5|Very Comfortable Pillow|qty=12}}}}
|price3=×3
"""
    rows = _workshop_rows_from_wikitext(wikitext)
    by_item = {name: qty for _s, _l, name, qty in rows}
    assert by_item["Apricot"] == 12
    assert by_item["Mushroom"] == 12
    assert by_item["Very Comfortable Pillow"] == 3


def test_workshop_list_and_itemgrid_merge_grid_overrides_list():
    """List macros plus ItemGrid: grid/price overwrites the same upgrade."""
    wikitext = """
* {{Workshop upgrade|Scrappy|level=5|Very Comfortable Pillow|qty=12}}
* {{Workshop upgrade|Gunsmith|level=2|Wasp Driver|qty=8}}
|name1={{#replace: Very Comfortable Pillow|{{Workshop upgrade|Scrappy|level=5|Very Comfortable Pillow|qty=12}}}}
|price1=×3
"""
    rows = _workshop_rows_from_wikitext(wikitext)
    by_key = {(s, l, n): q for s, l, n, q in rows}
    assert by_key[("Scrappy", 5, "Very Comfortable Pillow")] == 3
    assert by_key[("Gunsmith", 2, "Wasp Driver")] == 8


def test_outpost_room_expansion_keep_map_covers_chalet_mats():
    keep = outpost_room_expansion_keep_map()
    assert "Outpost Room 2 (3x)" in keep["epoxy bucket"]
    assert "Outpost Room 2 (3x)" in keep["hand drill"]
    assert "Outpost Room 2 (35x)" in keep["planks"]
    assert "Outpost Room 1 (3x)" in keep["steel cable"]


def test_apply_workshop_keep_map_replaces_loot_workshop_line():
    uses = {
        "very comfortable pillow": "Workshop: Scrappy 5 (12x)",
        "olives": "Workshop: Scrappy 4 (6x); Projects: Foo (1x)",
    }
    workshop = {
        "very comfortable pillow": ["Scrappy 5 (3x)"],
        "olives": ["Scrappy 4 (6x)"],
    }
    out = apply_workshop_keep_map(uses, workshop)
    assert out["very comfortable pillow"] == "Workshop: Scrappy 5 (3x)"
    assert "Projects: Foo (1x)" in out["olives"]
    assert "Scrappy 4 (6x)" in out["olives"]


def test_merge_action_upgrades_bare_recycle_when_keep_for_has_project():
    assert merge_action(
        "Recycle",
        "Keep until uses complete; recycle after",
        "Projects: Trophy Display (10x)",
    ) == "Keep until uses complete; recycle after"


def test_merge_action_preserves_hand_tuned_keep_action():
    assert merge_action(
        "KEEP FOR EXPEDITION MATS; Recycle after",
        "Keep until uses complete; recycle after",
        "Projects: Expedition 3 (3x)",
    ) == "KEEP FOR EXPEDITION MATS; Recycle after"


def test_merge_action_leaves_sell_when_keep_is_not_progress():
    assert merge_action(
        "Sell",
        "Use",
        "Quick Use consumable",
    ) == "Sell"


def test_merge_action_upgrades_sell_to_keep_for_weapons():
    assert merge_action(
        "Sell",
        "Keep",
        "Equippable Weapon",
    ) == "Keep"


def test_generate_action_weapon_and_blueprint():
    from update_db import generate_action

    weapon = GameItem(name="Canto I", category="Weapon", sell_price=7000, stack_size=1)
    assert generate_action(weapon) == ("Keep", "", "Equippable Weapon")

    blueprint = GameItem(
        name="Canto Blueprint", category="Blueprint", sell_price=5000, stack_size=1
    )
    assert generate_action(blueprint) == (
        "Keep",
        "",
        "Learn, sell, or trade to other players",
    )


def test_generate_action_assorted_seeds():
    from update_db import generate_action

    seeds = GameItem(
        name="Assorted Seeds", category="Nature", sell_price=100, stack_size=100
    )
    assert generate_action(seeds) == (
        "Sell or keep for user trading",
        "",
        "",
    )


def test_merge_keep_for_prefers_new():
    assert merge_keep_for("old note", "Quests: Foo (1x)") == "Quests: Foo (1x)"


def test_merge_keep_for_keeps_old_when_new_empty():
    assert merge_keep_for("hand-tuned note", "") == "hand-tuned note"


def test_build_csv_rows_merge_upgrades_bare_recycle_when_quest_keep():
    items = [
        GameItem(
            name="Bicycle Pump",
            category="Recyclable",
            recycles_to="4x Canister, 10x Metal Parts",
            uses="Quests: The League (1x)",
            sell_price=2000,
            stack_size=3,
        ),
    ]
    existing = {
        "bicycle pump": {
            "name": "Bicycle Pump",
            "action": "Recycle",
            "recycle_for": "4x Canister, 10x Metal Parts",
            "keep_for": "",  # previously missing quest note
            "sell_price": "2000",
            "stack_size": "3",
        },
    }
    rows, _stats = build_csv_rows(items, existing=existing, merge=True)
    assert len(rows) == 1
    # Bare Recycle contradicts quest keep_for → refresh to keep-until
    assert rows[0]["action"] == "Keep until uses complete; recycle after"
    assert rows[0]["keep_for"] == "Quests: The League (1x)"


def test_build_csv_rows_merge_overwrites_stale_keep_for():
    items = [
        GameItem(
            name="Battery",
            category="Basic Material",
            recycles_to="2x Metal Parts",
            uses="Quests: After Rain Comes (2x)",
            sell_price=250,
            stack_size=15,
        ),
    ]
    existing = {
        "battery": {
            "name": "Battery",
            "action": "Keep",
            "recycle_for": "2x Metal Parts",
            "keep_for": "old incomplete note",
            "sell_price": "250",
            "stack_size": "15",
        },
    }
    rows, _stats = build_csv_rows(items, existing=existing, merge=True)
    assert rows[0]["keep_for"] == "Quests: After Rain Comes (2x)"
    assert rows[0]["action"] == "Keep"
