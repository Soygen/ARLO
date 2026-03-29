"""Tests for station levels module."""

from arc_helper.database import Item
from arc_helper.stations import StationLevels
from arc_helper.stations import detect_fallback_action
from arc_helper.stations import parse_station_requirements
from arc_helper.stations import resolve_action


class TestParseStationRequirements:
    """Tests for parse_station_requirements function."""

    def test_single_station_single_level(self):
        """Parse simple single station requirement."""
        result = parse_station_requirements("6x for Gear Bench III Upgrade")
        assert result == [("gear_bench", 3)]

    def test_multiple_stations(self):
        """Parse multiple station requirements."""
        result = parse_station_requirements(
            "10x for Gear Bench III and Utility Station III Upgrades"
        )
        assert ("gear_bench", 3) in result
        assert ("utility_station", 3) in result

    def test_shared_level_format(self):
        """Parse shared level format with 'Level' keyword."""
        result = parse_station_requirements(
            "18x for Explosives, Medical Lab, and Utility Station Level I Upgrades"
        )
        assert ("explosives_station", 1) in result
        assert ("medical_lab", 1) in result
        assert ("utility_station", 1) in result

    def test_scrappy_multiple_levels(self):
        """Parse Scrappy with multiple levels (takes max)."""
        result = parse_station_requirements("17x for Scrappy Level III and V")
        assert result == [("scrappy", 5)]

    def test_skips_expedition(self):
        """Skip expedition references."""
        result = parse_station_requirements("5x Expedition Stage 4")
        assert result == []

    def test_skips_flickering_flames(self):
        """Skip Flickering Flames references."""
        result = parse_station_requirements("10x Flickering Flames Stage 3")
        assert result == []

    def test_mixed_with_semicolon(self):
        """Parse mixed requirements separated by semicolon."""
        result = parse_station_requirements(
            "5x for Gear Bench II Upgrade; 10x Expedition Stage 2"
        )
        assert result == [("gear_bench", 2)]

    def test_empty_string(self):
        """Handle empty string."""
        result = parse_station_requirements("")
        assert result == []


class TestDetectFallbackAction:
    """Tests for detect_fallback_action function."""

    def test_plain_keep(self):
        """Plain 'Keep' has no fallback."""
        assert detect_fallback_action("Keep") is None

    def test_plain_sell(self):
        """Plain 'Sell' has no fallback."""
        assert detect_fallback_action("Sell") is None

    def test_sell_once_done(self):
        """Detect 'sell once done' fallback."""
        assert (
            detect_fallback_action("Keep until upgrade is complete; sell once done")
            == "Sell"
        )

    def test_recycle_once_done(self):
        """Detect 'recycle once done' fallback."""
        assert (
            detect_fallback_action("Keep until upgrade is complete; recycle once done")
            == "Recycle"
        )

    def test_sell_afterwards(self):
        """Detect 'sell afterwards' fallback."""
        assert (
            detect_fallback_action("Keep for Scrappy Level V Upgrade; sell afterwards")
            == "Sell"
        )

    def test_recycle_if_short_skipped(self):
        """Skip 'recycle if short on materials' pattern."""
        assert (
            detect_fallback_action("Recycle if short on materials; Sell otherwise")
            is None
        )

    def test_sell_or_recycle(self):
        """Detect 'sell or recycle' pattern (first mentioned wins)."""
        assert detect_fallback_action("Keep; sell or recycle after") == "Sell"


class TestResolveAction:
    """Tests for resolve_action function."""

    def test_requirements_not_met(self):
        """Action stays 'Keep' when requirements not met."""
        item = Item(
            name="Test Item",
            action="Keep until upgrade is complete; sell once done",
            recycle_for="2x Metal Parts",
            keep_for="5x for Gear Bench III Upgrade",
            sell_price="1000",
        )
        levels = StationLevels(gear_bench=1)  # Level 1, needs 3
        result = resolve_action(item, levels)
        assert result.action == "Keep"
        assert result.keep_for == "5x for Gear Bench III Upgrade"

    def test_requirements_met_sell(self):
        """Action becomes 'Sell' when requirements met."""
        item = Item(
            name="Test Item",
            action="Keep until upgrade is complete; sell once done",
            recycle_for="2x Metal Parts",
            keep_for="5x for Gear Bench III Upgrade",
            sell_price="1000",
        )
        levels = StationLevels(gear_bench=3)  # Level 3, needs 3
        result = resolve_action(item, levels)
        assert result.action == "Sell"
        assert result.keep_for is None

    def test_preserves_sell_price(self):
        """Sell price is preserved in resolved item."""
        item = Item(
            name="Test Item",
            action="Keep until upgrade is complete; sell once done",
            recycle_for="2x Metal Parts",
            keep_for="5x for Gear Bench III Upgrade",
            sell_price="1500",
        )
        levels = StationLevels(gear_bench=3)
        result = resolve_action(item, levels)
        assert result.sell_price == "1500"

    def test_plain_keep_with_requirements_infers_recycle(self):
        """Plain 'Keep' with recycle_for infers Recycle fallback."""
        item = Item(
            name="Test Item",
            action="Keep",
            recycle_for="2x Metal Parts",
            keep_for="5x for Gear Bench III Upgrade",
            sell_price="1000",
        )
        levels = StationLevels(gear_bench=3)
        result = resolve_action(item, levels)
        assert result.action == "Recycle"

    def test_plain_keep_without_recycle_infers_sell(self):
        """Plain 'Keep' without recycle_for infers Sell fallback."""
        item = Item(
            name="Test Item",
            action="Keep",
            recycle_for=None,
            keep_for="5x for Gear Bench III Upgrade",
            sell_price="1000",
        )
        levels = StationLevels(gear_bench=3)
        result = resolve_action(item, levels)
        assert result.action == "Sell"

    def test_no_keep_for_unchanged(self):
        """Items without keep_for are returned unchanged."""
        item = Item(
            name="Test Item",
            action="Sell",
            recycle_for=None,
            keep_for=None,
            sell_price="500",
        )
        levels = StationLevels()
        result = resolve_action(item, levels)
        assert result.action == "Sell"
        assert result == item
