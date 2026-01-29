"""
Station levels module for Arc Raiders Helper.
Loads user-configured crafting station levels and resolves conditional item actions.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel
from pydantic import Field

from .config import APP_DIR
from .config import logger

if TYPE_CHECKING:
    from pathlib import Path

    from .database import Item

_YAML_AVAILABLE = True
try:
    import yaml
except ImportError:
    _YAML_AVAILABLE = False

STATION_LEVELS_FILE = APP_DIR / "station_levels.yaml"

# Roman numeral conversion
_ROMAN_TO_INT: dict[str, int] = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
}


class StationLevels(BaseModel):
    """User-configured crafting station levels."""

    gear_bench: int = Field(default=0, ge=0, le=3)
    gunsmith: int = Field(default=0, ge=0, le=3)
    medical_lab: int = Field(default=0, ge=0, le=3)
    explosives_station: int = Field(default=0, ge=0, le=3)
    utility_station: int = Field(default=0, ge=0, le=3)
    refiner: int = Field(default=0, ge=0, le=3)
    scrappy: int = Field(default=0, ge=0, le=5)


# Maps text patterns found in keep_for to StationLevels field names.
STATION_NAME_MAP: dict[str, str] = {
    "gear bench": "gear_bench",
    "gunsmith": "gunsmith",
    "medical lab": "medical_lab",
    "explosives station": "explosives_station",
    "explosive station": "explosives_station",
    "explosives": "explosives_station",
    "utility station": "utility_station",
    "refiner": "refiner",
    "scrappy": "scrappy",
}

# Ordered longest-first so "explosives station" matches before "explosives".
_STATION_PATTERNS: list[tuple[str, str]] = sorted(
    STATION_NAME_MAP.items(), key=lambda x: len(x[0]), reverse=True
)

# Patterns in keep_for that are NOT station references.
_NON_STATION_PATTERNS: list[str] = [
    "expedition",
    "flickering flames",
    "stage",
    "quest",
    "trader",
    ":",
]


def load_station_levels(path: Path | None = None) -> StationLevels:
    """Load station levels from YAML file. Returns all-zeros on any failure."""
    if path is None:
        path = STATION_LEVELS_FILE

    if not path.exists():
        logger.info("station_levels.yaml not found — station-level resolution disabled")
        return StationLevels()

    if not _YAML_AVAILABLE:
        logger.warning("pyyaml not installed — station-level resolution disabled")
        return StationLevels()

    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            logger.warning("station_levels.yaml: invalid format, using defaults")
            return StationLevels()
        stations = data.get("stations", data)
        if not isinstance(stations, dict):
            logger.warning(
                "station_levels.yaml: invalid 'stations' section, using defaults"
            )
            return StationLevels()
        return StationLevels(**stations)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to load station_levels.yaml: {exc} — using defaults")
        return StationLevels()


class StationLevelsSingleton:
    """Manages the singleton StationLevels instance."""

    _instance: StationLevels | None = None

    @classmethod
    def get(cls) -> StationLevels:
        if cls._instance is None:
            cls._instance = load_station_levels()
        return cls._instance

    @classmethod
    def reload(cls) -> StationLevels:
        cls._instance = load_station_levels()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None


def get_station_levels() -> StationLevels:
    return StationLevelsSingleton.get()


def _parse_roman(text: str) -> int | None:
    """Convert a roman numeral string (I-V) to int, or None."""
    return _ROMAN_TO_INT.get(text.strip().upper())


def parse_station_requirements(keep_for: str) -> list[tuple[str, int]]:
    """
    Extract (config_key, level) pairs from a keep_for string.

    Handles formats like:
      "6x for Gear Bench III Upgrade"
      "10x for Gear Bench III and Utility Station III Upgrades"
      "18x for Explosives, Medical Lab, and Utility Station Level I Upgrades"
      "17x for Scrappy Level III and V"

    Segments separated by ";" are processed independently so that
    non-station parts (Expedition, Flickering Flames, etc.) are skipped.
    """
    results: list[tuple[str, int]] = []

    # Split on ";" to handle compound keep_for values.
    segments = keep_for.split(";")

    for raw_segment in segments:
        stripped = raw_segment.strip()
        if not stripped:
            continue

        seg_lower = stripped.lower()

        # Skip segments that reference non-station things.
        if any(pat in seg_lower for pat in _NON_STATION_PATTERNS):
            continue

        _parse_segment(stripped, results)

    return results


def _parse_segment(segment: str, results: list[tuple[str, int]]) -> None:
    """Parse a single semicolon-delimited segment for station requirements."""
    seg_lower = segment.lower()

    # Try to find station names in the segment.
    found_stations: list[tuple[int, str]] = []  # (position, config_key)
    for pattern, config_key in _STATION_PATTERNS:
        pos = seg_lower.find(pattern)
        if pos != -1:
            found_stations.append((pos, config_key))

    if not found_stations:
        return

    # Sort by position in string.
    found_stations.sort()

    # Extract all roman numerals from the segment.
    roman_matches = list(re.finditer(r"\b(I{1,3}|IV|V)\b", segment))

    if not roman_matches:
        return

    # Check for "Level <roman>" pattern — shared level for all stations in segment.
    level_match = re.search(r"\bLevel\s+(I{1,3}|IV|V)\b", segment, re.IGNORECASE)

    if level_match:
        shared_level = _parse_roman(level_match.group(1))
        if shared_level is not None:
            for _, config_key in found_stations:
                results.append((config_key, shared_level))
        return

    # If there's only one station, collect all level numerals (for "Scrappy Level III and V").
    if len(found_stations) == 1:
        config_key = found_stations[0][1]
        levels: list[int] = []
        for m in roman_matches:
            lv = _parse_roman(m.group(1))
            if lv is not None:
                levels.append(lv)
        if levels:
            # Use the max level — user needs to reach the highest referenced upgrade.
            results.append((config_key, max(levels)))
        return

    # Multiple stations, each followed by its own roman numeral.
    # Match each station to the nearest following roman numeral.
    for station_pos, config_key in found_stations:
        best_match: re.Match[str] | None = None
        best_distance = float("inf")
        for m in roman_matches:
            if m.start() > station_pos and m.start() - station_pos < best_distance:
                best_distance = m.start() - station_pos
                best_match = m
        if best_match is not None:
            lv = _parse_roman(best_match.group(1))
            if lv is not None:
                results.append((config_key, lv))


def detect_fallback_action(action: str) -> str | None:
    """
    Detect the "then" action from conditional action text.

    Returns "Sell" or "Recycle" if the action is conditional, None if not.
    """
    # Normalize whitespace (handles multiline action text).
    normalized = " ".join(action.split()).strip()
    lower = normalized.lower()

    # Plain non-conditional actions.
    if lower in {"keep", "sell", "recycle", "use", "trash"}:
        return None

    # Non-station conditional patterns — skip these.
    if "recycle if short" in lower or "recycle if low" in lower:
        return None
    if "if no inventory" in lower or "if excess" in lower:
        return None
    if "keep for high-tier" in lower:
        return None

    # Detect the fallback action from various phrasings.
    if re.search(r"\bsell\s+once\s+done\b", lower):
        return "Sell"
    if re.search(r"\bsell\s+after\b", lower):
        return "Sell"
    if re.search(r"\bsell\s+afterwards\b", lower):
        return "Sell"
    if re.search(r"\brecycle\s+once\s+done\b", lower):
        return "Recycle"
    if re.search(r"\brecycle\s+after\b", lower):
        return "Recycle"
    if re.search(r"\brecycle\s+otherwise\b", lower):
        return "Recycle"

    # "sell or recycle" / "recycle or sell" — use first mentioned.
    sell_or_recycle = re.search(r"\b(sell|recycle)\s+or\s+(sell|recycle)\b", lower)
    if sell_or_recycle:
        first = sell_or_recycle.group(1)
        return first.capitalize()

    return None


def resolve_action(
    item: Item,
    station_levels: StationLevels,
) -> Item:
    """
    Resolve conditional item actions based on user's station levels.

    If the item's action is conditional (e.g. "Keep until upgrade is complete; sell once done")
    and the user has completed all referenced station upgrades, the action is changed to the
    fallback (Sell/Recycle). Otherwise, it's simplified to "Keep".

    Non-conditional items are returned unchanged.
    """
    from .database import Item as _Item

    fallback = detect_fallback_action(item.action)
    if fallback is None:
        return item

    if not item.keep_for:
        return item

    requirements = parse_station_requirements(item.keep_for)
    if not requirements:
        return item

    # Check if ALL station requirements are met.
    levels_dict = station_levels.model_dump()
    all_met = all(levels_dict.get(key, 0) >= level for key, level in requirements)

    if all_met:
        return _Item(
            name=item.name,
            action=fallback,
            recycle_for=item.recycle_for,
            keep_for=None,
        )

    return _Item(
        name=item.name,
        action="Keep",
        recycle_for=item.recycle_for,
        keep_for=item.keep_for,
    )
