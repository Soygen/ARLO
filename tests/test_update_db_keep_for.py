"""Tests for keep_for formatting and merge behavior in update_db."""

from update_db import GameItem
from update_db import build_csv_rows
from update_db import format_keep_for
from update_db import merge_action
from update_db import merge_keep_for


def test_format_keep_for_wiki_only():
    assert format_keep_for("3x for Gunsmith II Upgrade", []) == (
        "3x for Gunsmith II Upgrade"
    )


def test_format_keep_for_adds_projects_colon():
    assert format_keep_for("Projects Trophy Display (10x)", []) == (
        "Projects: Trophy Display (10x)"
    )


def test_format_keep_for_unescapes_html_entities():
    assert format_keep_for("Projects: Soul&#39;s Offering (15x)", []) == (
        "Projects: Soul's Offering (15x)"
    )


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
