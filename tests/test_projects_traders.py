"""Tests for wiki Projects/Traders catalog helpers."""

from pathlib import Path

from bs4 import BeautifulSoup

from arc_helper.database import Item
from arc_helper.progress import ProgressProfile
from arc_helper.progress import apply_progress
from arc_helper.projects import ProjectCatalog
from arc_helper.traders import TraderCatalog
from update_db import _aggregate_requirements
from update_db import _parse_wiki_item_quantities
from update_db import _requirements_from_tabbers
from update_db import _requirements_from_tables
from update_db import write_projects_catalog
from update_db import write_traders_catalog

TABBER_HTML = """
<div id="mw-content-text">
  <div class="tabber__panel" id="tabber-stage-1">
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
    assert ("ARC Alloy", 15) in pairs
    assert ("Rock Salt", 2) in pairs
    assert not any("Stiletto" in n for n, _ in pairs)


def test_requirements_from_requirement_table():
    root = BeautifulSoup(REQ_TABLE_HTML, "html.parser")
    pairs = _requirements_from_tables(root)
    assert _aggregate_requirements(pairs) == [
        ("Tick Pod", 15),
        ("Rusted Bolts", 3),
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
                    "item": {"id": "tick-pod", "name": "Tick Pod"},
                },
                {
                    "item_id": "wasp-driver",
                    "quantity": 20,
                    "item": {"id": "wasp-driver", "name": "Wasp Driver"},
                },
            ],
        }
    ]
    write_projects_catalog(
        projects,
        csv_path=tmp_path / "projects.csv",
        db_path=tmp_path / "projects.db",
    )
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
    assert len(rem) == 1 and rem[0].quantity == 20


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
