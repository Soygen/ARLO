"""Tests for MetaForge account-sync import and keep_for progress filtering."""

from pathlib import Path

from arc_helper.database import Item
from arc_helper.progress import apply_progress
from arc_helper.progress import import_account_sync
from arc_helper.progress import load_progress
from arc_helper.progress import save_progress
from arc_helper.progress import slugify

FIXTURE = Path(__file__).parent / "fixtures" / "metaforge_account_sync.json"


def test_slugify_quest_titles():
    assert slugify("Doctor's Orders") == "doctors-orders"
    assert slugify("Clearer Skies") == "clearer-skies"
    assert slugify("The Trifecta") == "the-trifecta"


def test_import_account_sync_quests_workshop_projects():
    profile = import_account_sync(FIXTURE)

    assert profile.account_id == "4780071058815716658"
    assert "clearer-skies" in profile.completed_quests
    assert "the-trifecta" in profile.completed_quests
    assert "collision-course" in profile.completed_quests
    # Not completed
    assert "doctors-orders" not in profile.completed_quests
    assert "the-league" not in profile.completed_quests
    assert "to-the-skies" not in profile.completed_quests

    assert profile.workshop_levels["gunsmith"] == 3
    assert profile.workshop_levels["medical lab"] == 2
    assert profile.workshop_levels["gear bench"] == 3
    assert profile.workshop_levels["scrappy"] == 3

    trophy = profile.project_items["trophy display"]
    assert trophy["tick-pod"] == "COMPLETED"
    assert trophy["wasp-driver"] == "IN_PROGRESS"

    sheltered = profile.project_items["sheltered retreat"]
    assert all(s == "COMPLETED" for s in sheltered.values())

    assert "anvil-recipe" in profile.owned_blueprints
    assert "ornate-stash-design" in profile.owned_blueprints


def test_save_and_load_roundtrip(tmp_path: Path):
    profile = import_account_sync(FIXTURE)
    path = tmp_path / "progress.json"
    save_progress(profile, path)
    loaded = load_progress(path)
    assert loaded is not None
    assert loaded.completed_quests == profile.completed_quests
    assert loaded.workshop_levels == profile.workshop_levels
    assert loaded.project_items == profile.project_items


def test_filter_completed_quest():
    profile = import_account_sync(FIXTURE)
    item = Item(
        name="ARC Alloy",
        action="Keep until uses complete; recycle after",
        recycle_for="2x Metal Parts",
        keep_for="Quests: Clearer Skies (3x) Doctor's Orders (2x)",
    )
    out = apply_progress(item, profile)
    assert "Clearer Skies" not in (out.keep_for or "")
    assert "Doctor's Orders" in (out.keep_for or "")
    # Still has a quest → keep-until action unchanged
    assert out.action == item.action


def test_filter_all_quests_rewrites_action():
    profile = import_account_sync(FIXTURE)
    item = Item(
        name="Snitch Scanner",
        action="Keep until uses complete; recycle after",
        recycle_for="1x Sensors",
        keep_for="Quests: The Trifecta (2x) Clearer Skies (3x)",
    )
    out = apply_progress(item, profile)
    assert not (out.keep_for or "").strip()
    assert out.action == "Recycle"


def test_filter_workshop_tier():
    profile = import_account_sync(FIXTURE)
    # Gunsmith is level 3 → Gunsmith 3 done; Medical Lab is 2 → Lab 3 still needed
    item = Item(
        name="Advanced Mechanical Components",
        action="Keep",
        keep_for="Workshop: Gunsmith 3 (5x) Medical Lab 3 (8x)",
    )
    out = apply_progress(item, profile)
    assert "Gunsmith" not in (out.keep_for or "")
    assert "Medical Lab 3" in (out.keep_for or "")


def test_filter_workshop_scrappy_independent_tiers():
    profile = import_account_sync(FIXTURE)
    # scrappy == 3 → Scrappy 3 done, Scrappy 5 still needed
    item = Item(
        name="Dog Collar",
        action="Keep until uses complete; sell after",
        keep_for="Workshop: Scrappy 3 (3x) Scrappy 5 (12x)",
    )
    out = apply_progress(item, profile)
    assert "Scrappy 3" not in (out.keep_for or "")
    assert "Scrappy 5" in (out.keep_for or "")
    assert out.action == item.action


def test_filter_project_item_completed():
    profile = import_account_sync(FIXTURE)
    item = Item(
        name="Tick Pod",
        action="Keep",
        keep_for="Projects: Trophy Display (15x)",
    )
    out = apply_progress(item, profile)
    assert not (out.keep_for or "").strip()


def test_filter_project_item_in_progress():
    profile = import_account_sync(FIXTURE)
    item = Item(
        name="Wasp Driver",
        action="Keep",
        keep_for="Projects: Trophy Display (15x)",
    )
    out = apply_progress(item, profile)
    assert "Trophy Display" in (out.keep_for or "")


def test_filter_project_fully_done_without_item_row():
    """Sheltered Retreat is fully COMPLETED — drop even if item slug missing."""
    profile = import_account_sync(FIXTURE)
    item = Item(
        name="Some Unrelated Mat",
        action="Keep until uses complete; sell after",
        keep_for="Projects: Sheltered Retreat (3x)",
    )
    out = apply_progress(item, profile)
    assert not (out.keep_for or "").strip()
    assert out.action == "Sell"


def test_unknown_project_fails_open():
    profile = import_account_sync(FIXTURE)
    item = Item(
        name="ARC Alloy",
        action="Keep",
        keep_for="Projects: Expedition 1 (80x)",
    )
    out = apply_progress(item, profile)
    assert "Expedition 1" in (out.keep_for or "")


def test_mixed_sections():
    profile = import_account_sync(FIXTURE)
    item = Item(
        name="Antiseptic",
        action="Keep",
        keep_for="Workshop: Medical Lab 3 (8x) Quests: Doctor's Orders (2x) Clearer Skies (3x)",
    )
    out = apply_progress(item, profile)
    assert "Medical Lab 3" in (out.keep_for or "")
    assert "Doctor's Orders" in (out.keep_for or "")
    assert "Clearer Skies" not in (out.keep_for or "")


def test_no_profile_unchanged():
    item = Item(
        name="ARC Alloy",
        action="Keep",
        keep_for="Quests: Clearer Skies (3x)",
    )
    assert apply_progress(item, None) is item


def test_cli_import(tmp_path: Path):
    from arc_helper.progress import main

    out = tmp_path / "progress.json"
    code = main(["import", str(FIXTURE), "-o", str(out), "-q"])
    assert code == 0
    assert out.is_file()
    loaded = load_progress(out)
    assert loaded is not None
    assert "clearer-skies" in loaded.completed_quests
