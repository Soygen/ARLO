"""Tests for overlay keep/recycle detail formatting."""

from arc_helper.overlay import RECYCLE_SYMBOL
from arc_helper.overlay import format_detail_parts
from arc_helper.overlay import format_detail_text
from arc_helper.overlay import normalize_keep_labels


def test_normalize_projects_quests_colon():
    assert normalize_keep_labels("Projects Trophy Display (10x)") == (
        "Projects: Trophy Display (10x)"
    )
    assert normalize_keep_labels("Quests The League (1x)") == "Quests: The League (1x)"
    assert normalize_keep_labels("Workshop Research Station 3 (6x)") == (
        "Workshop: Research Station 3 (6x)"
    )
    assert normalize_keep_labels("Projects: Already Ok (1x)") == "Projects: Already Ok (1x)"
    assert normalize_keep_labels("Quests [[Bait Fetch Blast]] (1x)") == (
        "Quests: Bait Fetch Blast (1x)"
    )


def test_keep_and_recycle_parts():
    keep, recycle = format_detail_parts(
        recycle_for="4x Canister, 10x Metal Parts",
        keep_for="Quests The League (1x)",
    )
    assert keep == "Quests: The League (1x)"
    assert recycle == f"{RECYCLE_SYMBOL} 4x Canister, 10x Metal Parts"


def test_keep_and_recycle_joined_text():
    text = format_detail_text(
        recycle_for="4x Canister, 10x Metal Parts",
        keep_for="Quests The League (1x)",
    )
    assert text == (
        f"Quests: The League (1x)\n"
        f"{RECYCLE_SYMBOL} 4x Canister, 10x Metal Parts"
    )


def test_keep_only():
    keep, recycle = format_detail_parts(
        recycle_for=None,
        keep_for="Quests: Unexpected Initiative (1x)",
    )
    assert keep == "Quests: Unexpected Initiative (1x)"
    assert recycle == ""


def test_recycle_only():
    keep, recycle = format_detail_parts(
        recycle_for="2x Metal Parts",
        keep_for="",
    )
    assert keep == ""
    assert recycle == f"{RECYCLE_SYMBOL} 2x Metal Parts"


def test_empty_both():
    assert format_detail_parts(recycle_for=None, keep_for=None) == ("", "")
    assert format_detail_text(recycle_for=None, keep_for=None) == ""


def test_whitespace_ignored():
    assert format_detail_parts(recycle_for="  ", keep_for="  ") == ("", "")
