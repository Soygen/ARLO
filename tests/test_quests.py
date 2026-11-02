"""Tests for quest catalog persistence and remaining-need totals."""

from pathlib import Path

from arc_helper.database import Item
from arc_helper.progress import ProgressProfile
from arc_helper.progress import apply_progress
from arc_helper.quests import QuestCatalog
from update_db import build_quest_catalog_rows
from update_db import quest_map_from_requirements
from update_db import rebuild_quests_database
from update_db import write_quests_csv

SAMPLE_QUESTS = [
    {
        "id": "after-rain-comes",
        "name": "After Rain Comes",
        "trader_name": "Tian Wen",
        "xp": 0,
        "manifest": {
            "prerequisites": [
                {"quest_id": "eyes-in-the-sky", "required_state": "COMPLETED"}
            ]
        },
        "required_items": [
            {
                "item_id": "wires-recipe",
                "quantity": 5,
                "item": {"id": "wires-recipe", "name": "Wires"},
            },
            {
                "item_id": "battery",
                "quantity": 2,
                "item": {"id": "battery", "name": "Battery"},
            },
        ],
    },
    {
        "id": "trash-into-treasure",
        "name": "Trash Into Treasure",
        "trader_name": "Celeste",
        "xp": 0,
        "manifest": {"prerequisites": []},
        "required_items": [
            {
                "item_id": "wires-recipe",
                "quantity": 6,
                "item": {"id": "wires-recipe", "name": "Wires"},
            },
        ],
    },
    {
        "id": "a-bad-feeling",
        "name": "A Bad Feeling",
        "trader_name": "Celeste",
        "xp": 0,
        "manifest": {
            "prerequisites": [
                {"quest_id": "off-the-raider", "required_state": "COMPLETED"}
            ]
        },
        "required_items": [],
    },
]

SAMPLE_TREE = {
    "parentRelationships": [
        {
            "quest_id": "after-rain-comes",
            "parent_quest_id": "eyes-in-the-sky",
            "required_state": "COMPLETED",
        },
        {
            "quest_id": "a-bad-feeling",
            "parent_quest_id": "off-the-raider",
            "required_state": "COMPLETED",
        },
    ]
}


def _build_catalog(tmp_path: Path) -> QuestCatalog:
    quest_rows, req_rows, prereq_rows = build_quest_catalog_rows(
        SAMPLE_QUESTS, SAMPLE_TREE
    )
    csv_path = tmp_path / "quests.csv"
    db_path = tmp_path / "quests.db"
    write_quests_csv(quest_rows, req_rows, csv_path)
    rebuild_quests_database(quest_rows, req_rows, prereq_rows, db_path)
    assert csv_path.is_file()
    return QuestCatalog(db_path)


def test_build_and_load_catalog(tmp_path: Path):
    catalog = _build_catalog(tmp_path)
    assert catalog.count() == 3
    assert catalog.has_quest("after-rain-comes")
    assert catalog.has_quest("After Rain Comes")

    reqs = catalog.requirements_for_item("Wires")
    assert len(reqs) == 2
    assert {r.quest_id for r in reqs} == {
        "after-rain-comes",
        "trash-into-treasure",
    }


def test_remaining_excludes_completed(tmp_path: Path):
    catalog = _build_catalog(tmp_path)
    remaining = catalog.remaining_for_item(
        "Wires",
        {"trash-into-treasure"},
    )
    assert len(remaining) == 1
    assert remaining[0].quest_id == "after-rain-comes"
    assert remaining[0].quantity == 5
    assert catalog.total_needed("Wires", {"trash-into-treasure"}) == 5


def test_quest_map_from_requirements():
    _quest_rows, req_rows, _ = build_quest_catalog_rows(SAMPLE_QUESTS, SAMPLE_TREE)
    qmap = quest_map_from_requirements(req_rows)
    assert "wires" in qmap
    assert any("After Rain Comes (5x)" in e for e in qmap["wires"])


def test_apply_progress_sets_quest_needed(tmp_path: Path):
    catalog = _build_catalog(tmp_path)
    profile = ProgressProfile(completed_quests={"trash-into-treasure"})
    item = Item(
        name="Wires",
        action="Keep",
        keep_for=(
            "Quests: After Rain Comes (5x) Trash Into Treasure (6x) "
            "Clamoring for Attention (3x)"
        ),
    )
    out = apply_progress(item, profile, catalog)
    # Catalog quests rebuilt; wiki-only Clamoring kept (not in sample catalog)
    assert "Trash Into Treasure" not in (out.keep_for or "")
    assert "After Rain Comes" in (out.keep_for or "")
    assert "Clamoring for Attention" in (out.keep_for or "")
    # Need sums every remaining listed qty (catalog 5 + wiki-only 3)
    assert out.quest_needed == 8


def test_apply_progress_no_remaining_clears_quest_needed(tmp_path: Path):
    catalog = _build_catalog(tmp_path)
    profile = ProgressProfile(
        completed_quests={"after-rain-comes", "trash-into-treasure"}
    )
    item = Item(
        name="Wires",
        action="Keep until uses complete; recycle after",
        recycle_for="2x Metal Parts",
        keep_for="Quests: After Rain Comes (5x) Trash Into Treasure (6x)",
    )
    out = apply_progress(item, profile, catalog)
    assert out.quest_needed is None
    assert "Quests:" not in (out.keep_for or "")
    assert out.action == "Crafting item"


def test_parse_wiki_item_quantities():
    from update_db import _parse_wiki_item_quantities

    assert _parse_wiki_item_quantities("—") == []
    assert _parse_wiki_item_quantities("3× ARC Alloy") == [("ARC Alloy", 3)]
    assert _parse_wiki_item_quantities("1× Battery 6× Wires") == [
        ("Battery", 1),
        ("Wires", 6),
    ]


def test_merge_quest_lists_unions_requirements():
    from update_db import merge_quest_lists

    primary = [
        {
            "id": "clearer-skies",
            "name": "Clearer Skies",
            "manifest": {"prerequisites": [{"quest_id": "x"}]},
            "required_items": [
                {
                    "item_id": "arc-alloy",
                    "quantity": 3,
                    "item": {"id": "arc-alloy", "name": "ARC Alloy"},
                }
            ],
        }
    ]
    secondary = [
        {
            "id": "clearer-skies",
            "name": "Clearer Skies",
            "required_items": [
                {
                    "item_id": "arc-alloy",
                    "quantity": 3,
                    "item": {"id": "arc-alloy", "name": "ARC Alloy"},
                }
            ],
        },
        {
            "id": "shoring-up-defenses",
            "name": "Shoring Up Defenses",
            "required_items": [
                {
                    "item_id": "arc-alloy",
                    "quantity": 1,
                    "item": {"id": "arc-alloy", "name": "ARC Alloy"},
                }
            ],
        },
    ]
    merged = merge_quest_lists(primary, secondary)
    by_id = {q["id"]: q for q in merged}
    assert "shoring-up-defenses" in by_id
    assert by_id["clearer-skies"]["manifest"]["prerequisites"]
