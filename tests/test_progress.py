"""Tests for MetaForge account-sync import and keep_for progress filtering."""

import json
from pathlib import Path

from arc_helper.database import Item
from arc_helper.progress import ProgressProfile
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

    assert profile.account_id == "4280071038815716698"
    assert profile.metaforge_synced_at == "2026-10-29T08:46:50.19+00:00"
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
    # Fixture has outpost.rooms == [] — independent of levels.outpost
    assert profile.workshop_levels["outpost room"] == 0

    trophy = profile.project_items["trophy display"]
    assert trophy["tick-pod"] == "COMPLETED"
    assert trophy["wasp-driver"] == "IN_PROGRESS"

    sheltered = profile.project_items["sheltered retreat"]
    assert all(s == "COMPLETED" for s in sheltered.values())

    assert "anvil-recipe" in profile.owned_blueprints
    assert "ornate-stash-design" in profile.owned_blueprints


def test_save_and_load_roundtrip(tmp_path: Path):
    profile = import_account_sync(FIXTURE)
    path = tmp_path / "account-sync.json"
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
    # Neutral — don't push Recycle once quest needs are done
    assert out.action == "Crafting item"


def test_crafting_keep_survives_completed_quest():
    item = Item(
        name="ARC Conductive Coupler",
        action="Keep",
        keep_for=(
            "Quests: Sizing Things Up (1x); Crafting: Emperor Gateway Conduit (1x)"
        ),
        sell_price=100,
    )
    out = apply_progress(item, ProgressProfile(completed_quests={"sizing-things-up"}))
    assert out.action == "Keep"
    assert out.keep_for == "Crafting: Emperor Gateway Conduit (1x)"


def test_hand_drill_outpost_done_becomes_crafting_item():
    """Crafting feedstock must not collapse to Recycle after workshop uses end."""
    profile = ProgressProfile(workshop_levels={"outpost room": 2})
    item = Item(
        name="Hand Drill",
        action="Keep until uses complete; recycle after",
        recycle_for="1x Mechanical Components",
        keep_for="Workshop: Outpost Room 2 (3x)",
        sell_price=640,
        stack_size=3,
    )
    out = apply_progress(item, profile)
    assert not (out.keep_for or "").strip()
    assert out.action == "Crafting item"


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


def test_bare_keep_becomes_sell_when_workshop_tier_done():
    profile = ProgressProfile(workshop_levels={"scrappy": 4})
    item = Item(
        name="Olives",
        action="Keep",
        keep_for="Workshop: Scrappy 4 (6x)",
        sell_price=640,
        stack_size=10,
    )
    out = apply_progress(item, profile)
    assert not (out.keep_for or "").strip()
    assert out.action == "Sell"


def test_filter_outpost_room_expansion_independent_of_outpost_level():
    """levels.outpost must not clear Outpost Room N; only rooms count does."""
    profile = ProgressProfile(
        workshop_levels={"outpost": 2, "outpost room": 1},
    )
    item = Item(
        name="Epoxy Bucket",
        action="Keep until uses complete; recycle after",
        keep_for="Workshop: Outpost Room 1 (3x) Outpost Room 2 (3x)",
        recycle_for="8x Chemicals",
    )
    out = apply_progress(item, profile)
    assert "Outpost Room 1" not in (out.keep_for or "")
    assert "Outpost Room 2 (3x)" in (out.keep_for or "")
    assert out.action == item.action


def test_wiki_outpost_project_cleared_by_outpost_workshop_level():
    """Wiki Projects: Outpost (mats) is Sheltered Retreat; done at outpost lvl ≥2."""
    profile = ProgressProfile(workshop_levels={"outpost": 3, "outpost room": 2})
    item = Item(
        name="ARC Alloy",
        action="Keep",
        keep_for=(
            "Projects: Expedition 1 (80x), Outpost (10x); "
            "Research: Rascal Blueprint (15x)"
        ),
    )
    out = apply_progress(item, profile)
    assert "Outpost (10x)" not in (out.keep_for or "")
    assert "Expedition 1 (80x)" in (out.keep_for or "")
    assert "Rascal Blueprint" in (out.keep_for or "")


def test_wiki_outpost_project_cleared_via_sheltered_retreat_progress():
    profile = ProgressProfile(
        project_items={
            "sheltered retreat": {"arc-alloy": "COMPLETED"},
        }
    )
    item = Item(
        name="ARC Alloy",
        action="Keep",
        keep_for="Projects: Outpost (10x)",
    )
    out = apply_progress(item, profile)
    assert not (out.keep_for or "").strip()


def test_wiki_outpost_still_needed_below_stage_level():
    profile = ProgressProfile(workshop_levels={"outpost": 1})
    item = Item(
        name="ARC Alloy",
        action="Keep",
        keep_for="Projects: Outpost (10x)",
    )
    out = apply_progress(item, profile)
    assert "Outpost (10x)" in (out.keep_for or "")


def test_filter_project_item_completed():
    profile = import_account_sync(FIXTURE)
    item = Item(
        name="Tick Pod",
        action="Keep",
        keep_for="Projects: Trophy Display (15x)",
    )
    out = apply_progress(item, profile)
    assert not (out.keep_for or "").strip()


def test_filter_staged_project_label_uses_bare_progress_bucket():
    """``Trophy Display 1`` must resolve to the ``trophy display`` progress bucket.

    Without that match, finished stage-1 mats (Surveyor Vault) stay listed while
    the player is already on later stages.
    """
    profile = ProgressProfile(
        project_items={
            "trophy display": {"surveyor-vault": "COMPLETED"},
        }
    )
    item = Item(
        name="Surveyor Vault",
        action="Keep until uses complete; recycle after",
        keep_for="Quests: Wires Crossed (1x); Projects: Trophy Display 1 (5x)",
    )
    out = apply_progress(item, profile)
    assert "Trophy Display" not in (out.keep_for or "")
    assert "Wires Crossed (1x)" in (out.keep_for or "")


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


def test_phased_project_prefers_in_progress_over_earlier_completed():
    """Same material in stage 1 COMPLETED + stage 2 IN_PROGRESS stays needed."""
    profile = import_account_sync(
        {
            "accountId": "1",
            "resources": {
                "projects": {
                    "data": {
                        "mappedProgress": {
                            "Soul's Offering::Phase 1/4::dead-flowers": {
                                "state": "COMPLETED",
                                "amount": 1,
                            },
                            "Soul's Offering::Phase 2/4::dead-flowers": {
                                "state": "IN_PROGRESS",
                                "amount": 2,
                            },
                            "Soul's Offering::dead-flowers": {
                                "state": "COMPLETED",
                                "amount": 1,
                            },
                        }
                    }
                }
            },
        }
    )
    assert profile.project_items["soul's offering"]["dead-flowers"] == "IN_PROGRESS"


def test_future_project_stage_item_not_cleared_when_earlier_stages_done(
    tmp_path: Path,
):
    """Mask Fragment (part 3) must stay while only parts 1–2 are mapped done."""
    from arc_helper.projects import ProjectCatalog
    from update_db import write_projects_catalog

    write_projects_catalog(
        [
            {
                "id": "souls-offering",
                "name": "Soul's Offering",
                "project_type": "Temporary",
                "starts_on": "2026-10-27",
                "ends_on": "2026-11-16",
                "required_items": [
                    {
                        "item_id": "mask-fragment",
                        "quantity": 1,
                        "stage": 3,
                        "item": {"id": "mask-fragment", "name": "Mask Fragment"},
                    },
                    {
                        "item_id": "arc-alloy",
                        "quantity": 15,
                        "stage": 1,
                        "item": {"id": "arc-alloy", "name": "ARC Alloy"},
                    },
                ],
            }
        ],
        csv_path=tmp_path / "projects.csv",
        db_path=tmp_path / "projects.db",
    )
    profile = ProgressProfile(
        project_items={
            "soul's offering": {
                "arc-alloy": "COMPLETED",
                "rock-salt": "COMPLETED",
                "dead-flowers": "COMPLETED",
            }
        }
    )
    item = Item(
        name="Mask Fragment",
        action="Keep until uses complete; sell after",
        keep_for="Projects: Soul's Offering 3 (1x)",
        sell_price=7000,
        stack_size=3,
    )
    out = apply_progress(
        item,
        profile,
        project_catalog=ProjectCatalog(tmp_path / "projects.db"),
    )
    assert "Soul's Offering 3" in (out.keep_for or "")
    assert out.action == item.action


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


def test_no_profile_still_sums_need_from_keep_for():
    item = Item(
        name="ARC Alloy",
        action="Keep",
        keep_for="Quests: Clearer Skies (3x)",
    )
    out = apply_progress(item, None)
    assert out.quest_needed == 3
    assert out.keep_for == item.keep_for


def test_load_raw_account_sync_body(tmp_path: Path):
    path = tmp_path / "account-sync.json"
    path.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    loaded = load_progress(path)
    assert loaded is not None
    assert loaded.account_id == "4280071038815716698"
    assert "clearer-skies" in loaded.completed_quests
    assert loaded.workshop_levels["gunsmith"] == 3


def test_cli_import(tmp_path: Path):
    from arc_helper.progress import main

    out = tmp_path / "account-sync.json"
    code = main(["import", str(FIXTURE), "-o", str(out), "-q"])
    assert code == 0
    assert out.is_file()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert "resources" in saved
    loaded = load_progress(out)
    assert loaded is not None
    assert "clearer-skies" in loaded.completed_quests


def test_blueprint_owned_normalizes_recipe_ids():
    from arc_helper.progress import _blueprint_owned

    profile = ProgressProfile(
        owned_blueprints={
            "anvil-recipe",
            "heavy-gun-parts-recipe",
            "aphelion-rifle-blueprint",
        }
    )
    assert _blueprint_owned(profile, "Anvil Blueprint")
    assert _blueprint_owned(profile, "Heavy Gun Parts Blueprint")
    assert _blueprint_owned(profile, "Aphelion Blueprint")
    assert not _blueprint_owned(profile, "Medium Gun Parts Blueprint")


def test_filter_research_keeps_unlearned_blueprints():
    profile = ProgressProfile(
        owned_blueprints={"heavy-gun-parts-recipe", "light-gun-parts-recipe"}
    )
    item = Item(
        name="Unusable Weapon",
        action="Keep until uses complete; recycle after",
        recycle_for="4x Metal Parts, 5x Simple Gun Parts",
        keep_for=(
            "Research: Heavy Gun Parts Blueprint (1x), "
            "Light Gun Parts Blueprint (1x), "
            "Medium Gun Parts Blueprint (1x), "
            "Tether Launcher Blueprint (1x)"
        ),
    )
    out = apply_progress(item, profile)
    assert "Heavy Gun Parts" not in (out.keep_for or "")
    assert "Light Gun Parts" not in (out.keep_for or "")
    assert "Medium Gun Parts Blueprint (1x)" in (out.keep_for or "")
    assert "Tether Launcher Blueprint (1x)" in (out.keep_for or "")
    assert out.project_needed == 2
    assert out.action == item.action


def test_import_includes_stencils():
    profile = import_account_sync(
        {
            "accountId": "1",
            "resources": {
                "blueprints": {
                    "data": {
                        "blueprints": [
                            {"name": "Anvil", "blueprintId": "anvil-recipe"}
                        ],
                        "designs": [
                            {"name": "Chandelier_01_A", "itemId": "chandelier-design"}
                        ],
                        "stencils": [
                            {
                                "name": "Unlock ItemSkin CommonRed",
                                "itemId": "garnet-stencil",
                            }
                        ],
                    }
                }
            },
        }
    )
    assert "anvil-recipe" in profile.owned_blueprints
    assert "chandelier-design" in profile.owned_blueprints
    assert "garnet-stencil" in profile.owned_blueprints


def test_overlay_marks_known_blueprint_design_stencil():
    profile = ProgressProfile(
        owned_blueprints={
            "anvil-recipe",
            "chandelier-design",
            "cerulean-stencil",
        }
    )
    known_bp = apply_progress(
        Item(
            name="Anvil Blueprint",
            action="Keep",
            keep_for="Learn, sell, or trade to other players",
        ),
        profile,
    )
    assert known_bp.action == "Sell"
    assert "Already know this blueprint" in (known_bp.keep_for or "")

    unknown_bp = apply_progress(
        Item(
            name="Tempest Blueprint",
            action="Keep",
            keep_for="Learn, sell, or trade to other players",
        ),
        profile,
    )
    assert unknown_bp.action == "Keep"
    assert "Not learned yet" in (unknown_bp.keep_for or "")

    known_design = apply_progress(
        Item(name="Chandelier Design", action="Sell", keep_for=""),
        profile,
    )
    assert known_design.action == "Sell"
    assert "Already know this design" in (known_design.keep_for or "")

    unknown_design = apply_progress(
        Item(name="Alpine Wardrobe Design", action="Sell", keep_for=""),
        profile,
    )
    assert unknown_design.action == "Keep"
    assert "Not learned yet" in (unknown_design.keep_for or "")

    known_stencil = apply_progress(
        Item(name="Cerulean Stencil", action="Sell", keep_for=""),
        profile,
    )
    assert "Already know this stencil" in (known_stencil.keep_for or "")
