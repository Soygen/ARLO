"""Tests for wiki Projects/Traders catalog helpers."""

from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from arc_helper.database import Item
from arc_helper.progress import ProgressProfile
from arc_helper.progress import apply_progress
from arc_helper.projects import ProjectCatalog
from arc_helper.traders import TraderCatalog
from update_db import _aggregate_requirements
from update_db import _parse_project_infobox
from update_db import _parse_wiki_item_quantities
from update_db import _requirements_from_tabbers
from update_db import _requirements_from_tables
from update_db import format_keep_for
from update_db import parse_wiki_date
from update_db import project_is_active
from update_db import write_projects_catalog
from update_db import write_traders_catalog

TABBER_HTML = """
<div id="mw-content-text">
  <a class="tabber__tab" role="tab" id="tabber-Construct_Vaultos_Offering_(1)-label"
     href="#tabber-Construct_Vaultos_Offering_(1)">Construct Vaulto's Offering (1)</a>
  <div class="tabber__panel" role="tabpanel" id="tabber-Construct_Vaultos_Offering_(1)"
       aria-labelledby="tabber-Construct_Vaultos_Offering_(1)-label">
    <ul>
      <li>15× ARC Alloy</li>
      <li>2× Rock Salt</li>
    </ul>
    <div class="mw-heading mw-heading3">Rewards</div>
    <ul><li>1× Stiletto II</li></ul>
  </div>
</div>
"""

REQ_TABLE_HTML = """
<div id="mw-content-text">
  <table>
    <tr><th>Requirement</th><th>Reward</th></tr>
    <tr><td>15× Tick Pod</td><td>10× Mod Components</td></tr>
    <tr><td>3× Rusted Bolts</td><td>3× Power Rod</td></tr>
  </table>
</div>
"""


def test_requirements_from_tabber_stops_at_rewards():
    root = BeautifulSoup(TABBER_HTML, "html.parser")
    pairs = _requirements_from_tabbers(root)
    assert ("ARC Alloy", 15, 1) in pairs
    assert ("Rock Salt", 2, 1) in pairs
    assert not any("Stiletto" in n for n, _, _ in pairs)


def test_requirements_from_requirement_table():
    root = BeautifulSoup(REQ_TABLE_HTML, "html.parser")
    pairs = _requirements_from_tables(root)
    assert _aggregate_requirements(pairs) == [
        ("Tick Pod", 15, None),
        ("Rusted Bolts", 3, None),
    ]


STAGED_TABBER_HTML = """
<div id="mw-content-text">
  <div class="tabber">
    <a class="tabber__tab" role="tab" id="tabber-Roaming_Threats_(1)-label"
       href="#tabber-Roaming_Threats_(1)">Roaming Threats (1)</a>
    <a class="tabber__tab" role="tab" id="tabber-Soaring_Menaces_(2)-label"
       href="#tabber-Soaring_Menaces_(2)">Soaring Menaces (2)</a>
    <div class="tabber__panel" role="tabpanel" id="tabber-Roaming_Threats_(1)"
         aria-labelledby="tabber-Roaming_Threats_(1)-label">
      <table>
        <tr><th>Requirement</th><th>Reward</th></tr>
        <tr><td>15× Tick Pod</td><td>10× Mod Components</td></tr>
        <tr><td>3× Rusted Bolts</td><td>3× Power Rod</td></tr>
      </table>
    </div>
    <div class="tabber__panel" role="tabpanel" id="tabber-Soaring_Menaces_(2)"
         aria-labelledby="tabber-Soaring_Menaces_(2)-label">
      <table>
        <tr><th>Requirement</th><th>Reward</th></tr>
        <tr><td>20× Wasp Driver</td><td>20× Raider Tokens</td></tr>
      </table>
    </div>
  </div>
</div>
"""


def test_staged_tabber_tables_keep_stage_numbers():
    root = BeautifulSoup(STAGED_TABBER_HTML, "html.parser")
    pairs = _requirements_from_tabbers(root)
    assert ("Tick Pod", 15, 1) in pairs
    assert ("Rusted Bolts", 3, 1) in pairs
    assert ("Wasp Driver", 20, 2) in pairs
    # Tables inside tabbers must not also appear unstaged
    assert _requirements_from_tables(root) == []
    assert _aggregate_requirements(pairs) == [
        ("Tick Pod", 15, 1),
        ("Rusted Bolts", 3, 1),
        ("Wasp Driver", 20, 2),
    ]


def test_write_and_query_projects(tmp_path: Path):
    projects = [
        {
            "id": "trophy-display",
            "name": "Trophy Display",
            "required_items": [
                {
                    "item_id": "tick-pod",
                    "quantity": 15,
                    "stage": 1,
                    "item": {"id": "tick-pod", "name": "Tick Pod"},
                },
                {
                    "item_id": "wasp-driver",
                    "quantity": 20,
                    "stage": 2,
                    "item": {"id": "wasp-driver", "name": "Wasp Driver"},
                },
            ],
        }
    ]
    project_map = write_projects_catalog(
        projects,
        csv_path=tmp_path / "projects.csv",
        db_path=tmp_path / "projects.db",
    )
    assert project_map["tick pod"] == ["Trophy Display 1 (15x)"]
    assert project_map["wasp driver"] == ["Trophy Display 2 (20x)"]
    catalog = ProjectCatalog(tmp_path / "projects.db")
    assert catalog.count() == 1
    rem = catalog.remaining_for_item(
        "Tick Pod",
        project_items={"trophy display": {"tick-pod": "COMPLETED"}},
    )
    assert rem == []
    rem = catalog.remaining_for_item(
        "Wasp Driver",
        project_items={"trophy display": {"wasp-driver": "IN_PROGRESS"}},
    )
    assert len(rem) == 1
    assert rem[0].quantity == 20
    # Later-stage item with no mappedProgress row yet — still remaining
    rem = catalog.remaining_for_item(
        "Wasp Driver",
        project_items={"trophy display": {"tick-pod": "COMPLETED"}},
    )
    assert len(rem) == 1
    assert rem[0].quantity == 20


def test_write_and_query_traders(tmp_path: Path):
    traders = [
        {
            "id": "celeste",
            "name": "Celeste",
            "items": [
                {"item_id": "wires", "item_name": "Wires", "price": "4"},
                {"item_id": "battery", "item_name": "Battery", "price": "1"},
            ],
        }
    ]
    write_traders_catalog(
        traders,
        csv_path=tmp_path / "traders.csv",
        db_path=tmp_path / "traders.db",
    )
    catalog = TraderCatalog(tmp_path / "traders.db")
    assert catalog.buy_hint("Wires") == "Celeste (4)"
    assert catalog.sellers_for_item("Battery")[0].trader_name == "Celeste"


def test_apply_progress_sets_buy_from_and_project_needed(tmp_path: Path):
    write_projects_catalog(
        [
            {
                "id": "souls-offering",
                "name": "Soul's Offering",
                "required_items": [
                    {
                        "item_id": "arc-alloy",
                        "quantity": 15,
                        "item": {"id": "arc-alloy", "name": "ARC Alloy"},
                    }
                ],
            }
        ],
        csv_path=tmp_path / "projects.csv",
        db_path=tmp_path / "projects.db",
    )
    write_traders_catalog(
        [
            {
                "id": "celeste",
                "name": "Celeste",
                "items": [
                    {
                        "item_id": "arc-alloy",
                        "item_name": "ARC Alloy",
                        "price": "12",
                    }
                ],
            }
        ],
        csv_path=tmp_path / "traders.csv",
        db_path=tmp_path / "traders.db",
    )
    profile = ProgressProfile(
        project_items={"soul's offering": {"arc-alloy": "IN_PROGRESS"}}
    )
    item = Item(name="ARC Alloy", action="Keep", keep_for="")
    out = apply_progress(
        item,
        profile,
        project_catalog=ProjectCatalog(tmp_path / "projects.db"),
        trader_catalog=TraderCatalog(tmp_path / "traders.db"),
    )
    assert out.project_needed == 15
    assert out.buy_from == "Celeste (12)"


def test_parse_qty_smoke():
    assert _parse_wiki_item_quantities("3× Planks") == [("Planks", 3)]


INFOBOX_HTML = """
<div id="mw-content-text">
  <table>
    <tr><th>Type</th><td>Temporary</td></tr>
    <tr><th>Duration</th><td>27 days</td></tr>
    <tr><th>Start</th><td>March 31, 2026</td></tr>
    <tr><th>End</th><td>April 27, 2026</td></tr>
  </table>
</div>
"""


def test_parse_project_infobox_dates():
    soup = BeautifulSoup(INFOBOX_HTML, "html.parser")
    info = _parse_project_infobox(soup)
    assert info["project_type"] == "Temporary"
    assert info["starts_on"] == "2026-03-31"
    assert info["ends_on"] == "2026-04-27"
    assert parse_wiki_date("March 31, 2026") == date(2026, 3, 31)


def test_expired_projects_excluded_from_keep_map_and_need(tmp_path: Path):
    projects = [
        {
            "id": "high-gain-antenna",
            "name": "High-Gain Antenna",
            "project_type": "Temporary",
            "starts_on": "2026-03-31",
            "ends_on": "2026-04-27",
            "required_items": [
                {
                    "item_id": "arc-alloy",
                    "quantity": 50,
                    "item": {"id": "arc-alloy", "name": "ARC Alloy"},
                }
            ],
        },
        {
            "id": "trophy-display",
            "name": "Trophy Display",
            "project_type": "",
            "starts_on": "",
            "ends_on": "",
            "required_items": [
                {
                    "item_id": "arc-alloy",
                    "quantity": 10,
                    "item": {"id": "arc-alloy", "name": "ARC Alloy"},
                }
            ],
        },
    ]
    assert not project_is_active(projects[0], today=date(2026, 10, 29))
    assert project_is_active(projects[1], today=date(2026, 10, 29))

    project_map = write_projects_catalog(
        projects,
        csv_path=tmp_path / "projects.csv",
        db_path=tmp_path / "projects.db",
        today=date(2026, 10, 29),
    )
    assert project_map["arc alloy"] == ["Trophy Display (10x)"]
    assert "High-Gain" not in " ".join(project_map["arc alloy"])

    catalog = ProjectCatalog(tmp_path / "projects.db")
    assert not catalog.is_active("High-Gain Antenna", today=date(2026, 10, 29))
    assert catalog.is_active("Trophy Display", today=date(2026, 10, 29))
    assert (
        catalog.total_needed("ARC Alloy", project_items={}, today=date(2026, 10, 29))
        == 10
    )

    item = Item(
        name="ARC Alloy",
        action="Keep",
        keep_for=("Projects: High-Gain Antenna (50x), Trophy Display (10x)"),
    )
    out = apply_progress(item, None, project_catalog=catalog)
    assert "High-Gain" not in (out.keep_for or "")
    assert "Trophy Display" in (out.keep_for or "")
    assert out.project_needed == 10


def test_format_keep_for_drops_inactive_wiki_projects():
    assert (
        format_keep_for(
            "Projects: Expedition 1 (5x) High-Gain Antenna (15x)",
            inactive_projects={"high-gain antenna"},
        )
        == "Projects: Expedition 1 (5x)"
    )


def test_format_keep_for_prefers_staged_project_labels():
    assert (
        format_keep_for(
            "Projects: Trophy Display (15x)",
            project_uses=["Trophy Display 2 (20x)"],
        )
        == "Projects: Trophy Display 2 (20x)"
    )
