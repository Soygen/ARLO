"""Player progress profile: MetaForge account-sync import + keep_for filtering.

Catalog rows still list every quest/workshop/project use. At overlay time we
strip finished requirements so recommendations match the player's state.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic import Field

from .database import Item
from .projects import ProjectCatalog
from .quests import QuestCatalog
from .traders import TraderCatalog

# ---------------------------------------------------------------------------
# Paths / persistence
# ---------------------------------------------------------------------------


def _default_path() -> Path:
    from .config import APP_DIR

    return APP_DIR / "progress.json"


class ProgressProfile(BaseModel):
    """Source-agnostic progress used by ``apply_progress``."""

    source: str = "metaforge-account-sync"
    account_id: str | None = None
    imported_at: str | None = None
    # Quest keys are slugified titles / MetaForge quest_ids (e.g. "clearer-skies")
    completed_quests: set[str] = Field(default_factory=set)
    # Display-ish station names lowercased → current level
    workshop_levels: dict[str, int] = Field(default_factory=dict)
    # project name lowercased → {item_slug: state} (COMPLETED / IN_PROGRESS / …)
    project_items: dict[str, dict[str, str]] = Field(default_factory=dict)
    # Owned blueprint / design itemIds (optional; unused by filter for now)
    owned_blueprints: set[str] = Field(default_factory=set)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Lowercase kebab key for quest ids and item names.

    Apostrophes are dropped (``Doctor's Orders`` → ``doctors-orders``) so
    titles match MetaForge ``quest_id`` values.
    """
    cleaned = (value or "").lower().replace("'", "").replace("'", "")
    return _NON_ALNUM.sub("-", cleaned).strip("-")


def _norm_name(value: str) -> str:
    """Case-folded display name for workshop / project matching."""
    return re.sub(r"\s+", " ", (value or "").strip().lower())


# MetaForge workshop.levels keys → keep_for display names
_WORKSHOP_KEY_TO_DISPLAY: dict[str, str] = {
    "outpost": "outpost",
    "refiner": "refiner",
    "scrappy": "scrappy",
    "gunsmith": "gunsmith",
    "gearbench": "gear bench",
    "gear_bench": "gear bench",
    "workbench": "workbench",
    "medicallab": "medical lab",
    "medical_lab": "medical lab",
    "utilitystation": "utility station",
    "utility_station": "utility station",
    "researchstation": "research station",
    "research_station": "research station",
    "explosivesstation": "explosives station",
    "explosives_station": "explosives station",
}


def _workshop_display_key(raw_key: str) -> str:
    compact = re.sub(r"[^a-z0-9]", "", raw_key.lower())
    if compact in _WORKSHOP_KEY_TO_DISPLAY:
        return _WORKSHOP_KEY_TO_DISPLAY[compact]
    # camelCase → spaced words: medicalLab → medical lab
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", raw_key)
    return _norm_name(spaced)


# ---------------------------------------------------------------------------
# Import MetaForge account-sync
# ---------------------------------------------------------------------------


def import_account_sync(raw: dict[str, Any] | str | Path) -> ProgressProfile:
    """Build a ``ProgressProfile`` from a MetaForge ``account-sync`` payload."""
    if isinstance(raw, (str, Path)):
        path = Path(raw)
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = raw

    resources = data.get("resources") or {}
    profile = ProgressProfile(
        source="metaforge-account-sync",
        account_id=data.get("accountId"),
        imported_at=datetime.now(UTC).isoformat(),
    )

    # Quests ---------------------------------------------------------------
    quests_block = (resources.get("quests") or {}).get("data") or {}
    for quest in quests_block.get("user_quests") or []:
        if not isinstance(quest, dict):
            continue
        if str(quest.get("status", "")).upper() != "COMPLETED":
            continue
        qid = quest.get("quest_id") or ""
        if qid:
            profile.completed_quests.add(slugify(str(qid)))

    # Workshop levels ------------------------------------------------------
    workshop_block = (resources.get("workshop") or {}).get("data") or {}
    levels = workshop_block.get("levels") or {}
    for key, level in levels.items():
        try:
            lvl = int(level)
        except (TypeError, ValueError):
            continue
        profile.workshop_levels[_workshop_display_key(str(key))] = lvl

    # Projects / mappedProgress --------------------------------------------
    projects_block = (resources.get("projects") or {}).get("data") or {}
    mapped = projects_block.get("mappedProgress") or {}
    for key, entry in mapped.items():
        if not isinstance(entry, dict):
            continue
        parts = str(key).split("::")
        if len(parts) < 2:
            continue
        project_name = _norm_name(parts[0])
        item_slug = slugify(parts[-1])
        state = str(entry.get("state") or "").upper() or "UNKNOWN"
        bucket = profile.project_items.setdefault(project_name, {})
        # Prefer COMPLETED if any duplicate Phase/non-Phase key says so
        prev = bucket.get(item_slug, "")
        if prev == "COMPLETED" or state == "COMPLETED":
            bucket[item_slug] = "COMPLETED"
        elif not prev or prev == "UNKNOWN":
            bucket[item_slug] = state

    # Blueprints (owned recipes/designs) -----------------------------------
    bp_block = (resources.get("blueprints") or {}).get("data") or {}
    for bp in bp_block.get("blueprints") or []:
        if isinstance(bp, dict) and bp.get("blueprintId"):
            profile.owned_blueprints.add(str(bp["blueprintId"]))
    for design in bp_block.get("designs") or []:
        if isinstance(design, dict) and design.get("itemId"):
            profile.owned_blueprints.add(str(design["itemId"]))

    return profile


def load_progress(path: Path | None = None) -> ProgressProfile | None:
    """Load ``progress.json``; return None if missing or unreadable."""
    target = path if path is not None else _default_path()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    # sets come back as lists from JSON
    if "completed_quests" in data and isinstance(data["completed_quests"], list):
        data["completed_quests"] = set(data["completed_quests"])
    if "owned_blueprints" in data and isinstance(data["owned_blueprints"], list):
        data["owned_blueprints"] = set(data["owned_blueprints"])
    return ProgressProfile.model_validate(data)


def save_progress(profile: ProgressProfile, path: Path | None = None) -> Path:
    """Persist profile as JSON (sets → sorted lists). Returns the path written."""
    target = path if path is not None else _default_path()
    payload = profile.model_dump()
    payload["completed_quests"] = sorted(profile.completed_quests)
    payload["owned_blueprints"] = sorted(profile.owned_blueprints)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


# ---------------------------------------------------------------------------
# keep_for parsing / filtering
# ---------------------------------------------------------------------------

_SECTION_SPLIT = re.compile(
    r"(?=(?:Workshop|Quests|Projects):)",
    re.IGNORECASE,
)
_SECTION_HEAD = re.compile(
    r"^(Workshop|Quests|Projects):\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)
# "Medical Lab 3 (8x)" / "Doctor's Orders (2x)" / "Trophy Display (10x)"
_ENTRY_RE = re.compile(
    r"(.+?)\s*\((\d+)x\)",
)
# Workshop entries always end with a tier digit before (Nx)
_WORKSHOP_ENTRY_RE = re.compile(
    r"^(.+?)\s+(\d+)$",
)

_PROGRESS_KEEP_RE = re.compile(
    r"quests?|projects?|workshop|upgrade|expedition|scrappy|"
    r"flickering\s+flames|\bstage\b",
    re.IGNORECASE,
)


def has_progress_keep(keep_for: str) -> bool:
    """True when keep_for names a quest, project, workshop, or upgrade use."""
    return bool(_PROGRESS_KEEP_RE.search(keep_for or ""))


def _parse_entries(section_body: str) -> list[tuple[str, int]]:
    """Split a section body into ``(label, qty)`` pairs via ``(Nx)`` boundaries."""
    return [
        (m.group(1).strip(), int(m.group(2)))
        for m in _ENTRY_RE.finditer(section_body or "")
        if m.group(1).strip()
    ]


def _format_entries(entries: list[tuple[str, int]]) -> str:
    return " ".join(f"{label} ({qty}x)" for label, qty in entries)


def _quest_done(profile: ProgressProfile, quest_label: str) -> bool:
    return slugify(quest_label) in profile.completed_quests


def _workshop_done(profile: ProgressProfile, label: str) -> bool:
    """True when player's station level already meets the listed tier."""
    m = _WORKSHOP_ENTRY_RE.match(label.strip())
    if not m:
        return False
    station = _norm_name(m.group(1))
    try:
        needed = int(m.group(2))
    except ValueError:
        return False
    current = profile.workshop_levels.get(station)
    if current is None:
        # try compact match (gearbench vs gear bench)
        compact = re.sub(r"[^a-z0-9]", "", station)
        for key, lvl in profile.workshop_levels.items():
            if re.sub(r"[^a-z0-9]", "", key) == compact:
                current = lvl
                break
    if current is None:
        return False
    return current >= needed


def _project_clause_done(
    profile: ProgressProfile,
    project_label: str,
    *,
    item_name: str,
) -> bool:
    """True when this project clause no longer applies for ``item_name``."""
    project = _norm_name(project_label)
    bucket = profile.project_items.get(project)
    if not bucket:
        # Try prefix match for "Expedition 1" style names vs missing data
        for key, value in profile.project_items.items():
            if key == project or key.startswith(project + " "):
                bucket = value
                break
    if not bucket:
        return False  # fail open — no mapped progress for this project

    item_slug = slugify(item_name)
    if item_slug in bucket:
        return bucket[item_slug] == "COMPLETED"

    # No item-specific row: drop only if every tracked requirement is done
    return bool(bucket) and all(state == "COMPLETED" for state in bucket.values())


def _filter_quest_entries(
    entries: list[tuple[str, int]],
    profile: ProgressProfile,
    *,
    item_name: str,
    catalog: QuestCatalog | None,
) -> tuple[list[tuple[str, int]], int | None]:
    """Filter quest keep clauses; prefer structured catalog when available.

    Returns ``(kept_entries, quest_needed_total)``. ``quest_needed_total`` is
    the sum of remaining catalog requirements for this item (None if no catalog
    hit). Wiki-only quest names not in the catalog are kept via string filter.
    """
    if catalog is not None and catalog.available:
        remaining = catalog.remaining_for_item(item_name, profile.completed_quests)
        catalog_all = catalog.requirements_for_item(item_name)
        if catalog_all:
            kept = [(r.quest_name, r.quantity) for r in remaining]
            covered_ids = {r.quest_id for r in catalog_all}
            covered_names = {r.quest_name.lower() for r in catalog_all}
            # Preserve wiki-only extras that the API catalog doesn't list
            for label, qty in entries:
                if _quest_done(profile, label):
                    continue
                if slugify(label) in covered_ids or label.lower() in covered_names:
                    continue
                if catalog.has_quest(label) or catalog.has_quest(slugify(label)):
                    # Known quest but no requirement row for this item — drop
                    continue
                kept.append((label, qty))
            total = sum(r.quantity for r in remaining)
            return kept, total

    kept = [(label, qty) for label, qty in entries if not _quest_done(profile, label)]
    return kept, None


def _filter_keep_for(
    keep_for: str,
    profile: ProgressProfile,
    *,
    item_name: str,
    catalog: QuestCatalog | None = None,
) -> tuple[str, int | None]:
    text = (keep_for or "").strip()
    if not text:
        return "", None

    chunks = [c for c in _SECTION_SPLIT.split(text) if c.strip()]
    preamble: list[str] = []
    rebuilt: list[str] = []
    quest_needed: int | None = None
    saw_quests = False

    for chunk in chunks:
        head = _SECTION_HEAD.match(chunk.strip())
        if not head:
            preamble.append(chunk.strip())
            continue
        kind = head.group(1).capitalize()
        if kind.lower() == "quests":
            kind = "Quests"
        elif kind.lower() == "workshop":
            kind = "Workshop"
        elif kind.lower() == "projects":
            kind = "Projects"

        entries = _parse_entries(head.group(2))
        if kind == "Quests":
            saw_quests = True
            kept, quest_needed = _filter_quest_entries(
                entries,
                profile,
                item_name=item_name,
                catalog=catalog,
            )
        else:
            kept = []
            for label, qty in entries:
                if kind == "Workshop" and _workshop_done(profile, label):
                    continue
                if kind == "Projects" and _project_clause_done(
                    profile, label, item_name=item_name
                ):
                    continue
                kept.append((label, qty))
        if kept:
            rebuilt.append(f"{kind}: {_format_entries(kept)}")

    # Item may have catalog requirements even when keep_for had no Quests: section
    if (
        not saw_quests
        and catalog is not None
        and catalog.available
        and catalog.requirements_for_item(item_name)
    ):
        kept, quest_needed = _filter_quest_entries(
            [],
            profile,
            item_name=item_name,
            catalog=catalog,
        )
        if kept:
            rebuilt.insert(0, f"Quests: {_format_entries(kept)}")

    parts = [p for p in (*preamble, *rebuilt) if p]
    return " ".join(parts), quest_needed


def _rewrite_action(action: str, recycle_for: str | None, keep_for: str) -> str:
    """When all progress clauses are gone, collapse keep-until wording."""
    if has_progress_keep(keep_for):
        return action
    al = (action or "").lower()
    sells = "sell after" in al or "sell once" in al
    recycles = (
        "recycle after" in al
        or "recycle once" in al
        or "recycle or sell" in al
        or "sell or recycle" in al
    )
    if sells and not recycles:
        return "Sell"
    if recycles:
        return "Recycle" if (recycle_for or "").strip() else "Sell"
    if sells:
        return "Sell"
    return action


def apply_progress(
    item: Item,
    profile: ProgressProfile | None,
    catalog: QuestCatalog | None = None,
    project_catalog: ProjectCatalog | None = None,
    trader_catalog: TraderCatalog | None = None,
) -> Item:
    """Return a copy of ``item`` with finished progress stripped from keep_for.

    When ``catalog`` is provided, quest requirements come from ``data/arlo.db``
    (minus completed), and ``quest_needed`` is set to the remaining total qty.
    ``project_catalog`` / ``trader_catalog`` fill ``project_needed`` and
    ``buy_from``. Unrecognized clauses fail open (kept).
    """
    buy_from = None
    if trader_catalog is not None and trader_catalog.available:
        buy_from = trader_catalog.buy_hint(item.name)

    empty_projects: dict[str, dict[str, str]] = {}
    if profile is None:
        updates: dict[str, object] = {}
        if catalog is not None and catalog.available:
            total = catalog.total_needed(item.name, set())
            if total > 0:
                updates["quest_needed"] = total
        if project_catalog is not None and project_catalog.available:
            pt = project_catalog.total_needed(
                item.name, project_items=empty_projects
            )
            if pt > 0:
                updates["project_needed"] = pt
        if buy_from:
            updates["buy_from"] = buy_from
        return item.model_copy(update=updates) if updates else item

    filtered, quest_needed = _filter_keep_for(
        item.keep_for or "",
        profile,
        item_name=item.name,
        catalog=catalog,
    )
    if quest_needed is None and catalog is not None and catalog.available:
        quest_needed = catalog.total_needed(item.name, profile.completed_quests) or None

    project_needed: int | None = None
    if project_catalog is not None and project_catalog.available:
        project_needed = project_catalog.total_needed(
            item.name, project_items=profile.project_items
        ) or None

    updates = {}
    if filtered != (item.keep_for or "").strip():
        updates["keep_for"] = filtered or None
        updates["action"] = _rewrite_action(
            item.action, item.recycle_for, filtered
        )
    if quest_needed is not None:
        updates["quest_needed"] = quest_needed if quest_needed > 0 else None
    elif catalog is not None and catalog.requirements_for_item(item.name):
        updates["quest_needed"] = None
    if project_needed is not None:
        updates["project_needed"] = project_needed if project_needed > 0 else None
    if buy_from:
        updates["buy_from"] = buy_from

    if not updates:
        return item
    return item.model_copy(update=updates)


# ---------------------------------------------------------------------------
# CLI: python -m arc_helper.progress import path/to/account-sync.json
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="arc-import-progress",
        description="Import MetaForge account-sync JSON into progress.json",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    imp = sub.add_parser("import", help="Import an account-sync JSON file")
    imp.add_argument(
        "file",
        type=Path,
        help="Path to saved MetaForge account-sync response JSON",
    )
    imp.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output path (default: progress.json next to the app)",
    )
    imp.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only print the output path",
    )

    show = sub.add_parser("show", help="Print the current progress profile")
    show.add_argument(
        "-i",
        "--input",
        type=Path,
        default=None,
        help="progress.json path (default: app progress.json)",
    )

    args = parser.parse_args(argv)

    if args.cmd == "import":
        if not args.file.is_file():
            print(f"error: file not found: {args.file}", file=sys.stderr)
            return 1
        profile = import_account_sync(args.file)
        out = save_progress(profile, args.output)
        if args.quiet:
            print(out)
            return 0
        print(f"Wrote {out}")
        print(f"  account:     {profile.account_id or '(unknown)'}")
        print(f"  quests done: {len(profile.completed_quests)}")
        print(f"  workshop:    {len(profile.workshop_levels)} stations")
        print(f"  projects:    {len(profile.project_items)} tracked")
        print(f"  blueprints:  {len(profile.owned_blueprints)}")
        return 0

    if args.cmd == "show":
        profile = load_progress(args.input)
        if profile is None:
            print("No progress.json found", file=sys.stderr)
            return 1
        payload = profile.model_dump()
        payload["completed_quests"] = sorted(profile.completed_quests)
        payload["owned_blueprints"] = sorted(profile.owned_blueprints)
        print(json.dumps(payload, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
