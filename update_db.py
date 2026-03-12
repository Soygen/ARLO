#!/usr/bin/env python3
"""
MetaForge API & Database Updater for ARLO (Arc Raiders Loot Overlay).

Fetches item data from the MetaForge API (https://metaforge.app/arc-raiders)
and updates items.csv + items.db with action recommendations.

Falls back to the Arc Raiders Wiki if the API is unavailable.

Usage:
    python update_db.py                 # Full update (overwrites items.csv)
    python update_db.py --merge         # Merge: keep existing manual overrides
    python update_db.py --dry-run       # Preview without writing files
    python update_db.py --csv-only      # Only update items.csv, skip DB rebuild
    python update_db.py --source wiki   # Force wiki scraper instead of API
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print(
        "Missing dependencies. Install them with:\n"
        "  pip install requests beautifulsoup4\n"
        "  -- or --\n"
        "  uv add requests beautifulsoup4"
    )
    sys.exit(1)


# =============================================================================
# Constants
# =============================================================================

METAFORGE_API_BASE = "https://metaforge.app/api/arc-raiders"
METAFORGE_SUPABASE_URL = "https://unhbvkszwhczbjxgetgk.supabase.co/rest/v1"
# MetaForge's public Supabase anonymous key - client-accessible, visible in their website source
METAFORGE_SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVuaGJ2a3N6d2hjemJqeGdldGdrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDQ5NjgwMjUsImV4cCI6MjA2MDU0NDAyNX0."
    "gckCmxnlpwwJOGmc5ebLYDnaWaxr5PW31eCrSPR5aRQ"
)

WIKI_URL = "https://arcraiders.wiki/wiki/Loot"
SCRIPT_DIR = Path(__file__).parent.resolve()
ITEMS_CSV = SCRIPT_DIR / "items.csv"
ITEMS_DB = SCRIPT_DIR / "items.db"

USER_AGENT = "ARLO/1.0 (Arc Raiders Loot Overlay - https://github.com/Soygen/ARLO)"

QUICK_USE_CATEGORIES = {"Quick Use", "Shield", "Augment", "Mods", "Ammunition", "Key"}
MATERIAL_CATEGORIES = {"Basic Material", "Refined Material", "Topside Material"}


# =============================================================================
# Data classes
# =============================================================================

@dataclass
class GameItem:
    """An item from any data source, normalized to a common format."""

    name: str
    rarity: str = ""
    recycles_to: str = ""
    sell_price: int = 0
    stack_size: int = 0
    category: str = ""
    uses: str = ""

    action: str = ""
    recycle_for: str = ""
    keep_for: str = ""


@dataclass
class ScraperStats:
    """Track update results."""

    items_scraped: int = 0
    items_written: int = 0
    items_preserved: int = 0
    items_new: int = 0
    source: str = ""
    errors: list[str] = field(default_factory=list)


# =============================================================================
# MetaForge API source
# =============================================================================

def _api_get(url: str, *, timeout: int = 30):
    """Make a GET request to the MetaForge API."""
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.json()


def _supabase_get(table: str, params: str = "") -> list:
    """Fetch data from MetaForge's public Supabase tables."""
    url = f"{METAFORGE_SUPABASE_URL}/{table}"
    if params:
        url += f"?{params}"
    resp = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent": USER_AGENT,
            "apikey": METAFORGE_SUPABASE_KEY,
            "Authorization": f"Bearer {METAFORGE_SUPABASE_KEY}",
        },
    )
    resp.raise_for_status()
    return resp.json()


def fetch_metaforge_items() -> list[dict]:
    """Fetch all items from the MetaForge API with pagination."""
    print("Fetching items from MetaForge API...")
    all_items: list[dict] = []
    page = 1
    limit = 100

    while True:
        url = f"{METAFORGE_API_BASE}/items?page={page}&limit={limit}"
        data = _api_get(url)
        items = data.get("data", [])
        pagination = data.get("pagination", {})

        all_items.extend(items)
        total = pagination.get("total", "?")
        print(f"  Page {page}: {len(items)} items (total: {len(all_items)}/{total})")

        if not pagination.get("hasNextPage", False):
            break
        page += 1
        time.sleep(0.1)

    print(f"  Fetched {len(all_items)} items from API")
    return all_items


def fetch_metaforge_recycle_map(all_items: list[dict]) -> dict[str, str]:
    """Fetch recycle component data and build item_id -> "2x Metal Parts, 3x Wires" map."""
    print("Fetching recycle data from MetaForge...")
    try:
        components = _supabase_get("arc_item_recycle_components", "select=*")
    except Exception as e:
        print(f"  Recycle data unavailable ({e}), continuing without it")
        return {}

    id_to_name: dict[str, str] = {}
    for item in all_items:
        item_id = item.get("id", "")
        name = item.get("name", "")
        if item_id and name:
            id_to_name[item_id] = name

    recycle_raw: dict[str, list[tuple[str, int]]] = {}
    for comp in components:
        item_id = comp.get("item_id", "")
        comp_id = comp.get("component_id", "")
        qty = comp.get("quantity", 0)
        if item_id and comp_id:
            recycle_raw.setdefault(item_id, []).append((comp_id, qty))

    recycle_map: dict[str, str] = {}
    for item_id, parts in recycle_raw.items():
        part_strs = []
        for comp_id, qty in parts:
            comp_name = id_to_name.get(comp_id, comp_id)
            part_strs.append(f"{qty}x {comp_name}")
        recycle_map[item_id] = ", ".join(part_strs)

    print(f"  Loaded recycle data for {len(recycle_map)} items")
    return recycle_map


def fetch_metaforge_quest_items() -> dict[str, list[str]]:
    """Fetch quests and build item_name -> [quest names] map."""
    print("Fetching quests from MetaForge API...")
    all_quests: list[dict] = []
    page = 1
    limit = 100

    while True:
        url = f"{METAFORGE_API_BASE}/quests?page={page}&limit={limit}"
        try:
            data = _api_get(url)
        except Exception as e:
            print(f"  Quest data unavailable ({e}), continuing without it")
            return {}

        quests = data.get("data", [])
        pagination = data.get("pagination", {})
        all_quests.extend(quests)

        if not pagination.get("hasNextPage", False):
            break
        page += 1
        time.sleep(0.1)

    quest_item_map: dict[str, list[str]] = {}
    for quest in all_quests:
        quest_name = quest.get("name", "")
        required = quest.get("required_items", [])
        if not quest_name or not required:
            continue
        for req in required:
            if isinstance(req, dict):
                item_name = req.get("name", "")
                qty = req.get("quantity", req.get("count", 1))
                if item_name:
                    quest_item_map.setdefault(item_name.lower(), []).append(
                        f"{quest_name} ({qty}x)"
                    )
            elif isinstance(req, str) and req:
                quest_item_map.setdefault(req.lower(), []).append(quest_name)

    print(f"  Mapped quest requirements for {len(quest_item_map)} items")
    return quest_item_map


def _normalize_category(item_type: str) -> str:
    """Map MetaForge item_type values to our category names."""
    mapping = {
        "basic_material": "Basic Material",
        "refined_material": "Refined Material",
        "topside_material": "Topside Material",
        "recyclable": "Recyclable",
        "trinket": "Trinket",
        "nature": "Nature",
        "quick_use": "Quick Use",
        "key": "Key",
        "ammunition": "Ammunition",
        "ammo": "Ammunition",
        "shield": "Shield",
        "augment": "Augment",
        "mods": "Mods",
        "mod": "Mods",
    }
    normalized = item_type.lower().strip()
    return mapping.get(normalized, item_type.title() if item_type else "")


def fetch_items_from_metaforge() -> list[GameItem]:
    """Fetch all item data from MetaForge API + Supabase and return GameItem objects."""
    raw_items = fetch_metaforge_items()
    recycle_map = fetch_metaforge_recycle_map(raw_items)
    quest_map = fetch_metaforge_quest_items()

    items: list[GameItem] = []
    for raw in raw_items:
        item_id = raw.get("id", "")
        name = raw.get("name", "")
        if not name:
            continue

        stat_block = raw.get("stat_block") or {}
        category = _normalize_category(raw.get("item_type", ""))
        sell_price = raw.get("value", 0) or 0
        stack_size = stat_block.get("stackSize", 0) or 0
        rarity = raw.get("rarity", "")

        uses_parts: list[str] = []
        quest_uses = quest_map.get(name.lower(), [])
        if quest_uses:
            uses_parts.append("Quests: " + ", ".join(quest_uses))
        uses = "; ".join(uses_parts)

        recycles_to = recycle_map.get(item_id, "")

        items.append(GameItem(
            name=name,
            rarity=rarity,
            recycles_to=recycles_to,
            sell_price=sell_price,
            stack_size=stack_size,
            category=category,
            uses=uses,
        ))

    print(f"  Processed {len(items)} items from MetaForge")
    return items


# =============================================================================
# Wiki scraper source (fallback)
# =============================================================================

def fetch_wiki_page() -> str:
    """Fetch the loot page HTML from the wiki."""
    print(f"Fetching {WIKI_URL} ...")
    resp = requests.get(WIKI_URL, timeout=30, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    print(f"  Got {len(resp.text):,} bytes")
    return resp.text


def _parse_recycles_to(cell) -> str:
    if not cell:
        return ""
    text = cell.get_text(" ", strip=True)
    if "cannot" in text.lower() or "n/a" in text.lower():
        return ""
    text = text.replace("\u00d7", "x")
    parts = re.split(r"(?<=\S)\s+(?=\d+x\s)", text)
    return ", ".join(p.strip() for p in parts if p.strip())


def _parse_uses(cell) -> str:
    if not cell:
        return ""
    text = cell.get_text(" ", strip=True)
    if not text:
        return ""
    text = text.replace("\u00d7", "x")
    return re.sub(r"\s+", " ", text).strip()


def _parse_int_cell(cell) -> int:
    if not cell:
        return 0
    text = cell.get_text(strip=True).replace(",", "").strip()
    try:
        return int(text)
    except ValueError:
        return 0


def fetch_items_from_wiki() -> list[GameItem]:
    """Parse the wiki loot table and return GameItem objects."""
    html = fetch_wiki_page()
    soup = BeautifulSoup(html, "html.parser")

    loot_table = None
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if "item" in headers and "rarity" in headers and "recycles to" in headers:
            loot_table = table
            break

    if not loot_table:
        print("ERROR: Could not find the loot table on the wiki page!")
        return []

    header_row = loot_table.find("tr")
    headers = [th.get_text(strip=True).lower() for th in header_row.find_all("th")]

    col_map = {}
    for i, h in enumerate(headers):
        if h == "item":
            col_map["item"] = i
        elif h == "rarity":
            col_map["rarity"] = i
        elif "recycles" in h:
            col_map["recycles_to"] = i
        elif "sell" in h:
            col_map["sell_price"] = i
        elif "stack" in h:
            col_map["stack_size"] = i
        elif h == "category":
            col_map["category"] = i
        elif h == "uses":
            col_map["uses"] = i

    print(f"  Found columns: {list(col_map.keys())}")
    items: list[GameItem] = []

    for row in loot_table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        def get_cell(key: str):
            idx = col_map.get(key)
            if idx is not None and idx < len(cells):
                return cells[idx]
            return None

        item_cell = get_cell("item")
        if not item_cell:
            continue

        name_link = item_cell.find("a")
        name = name_link.get_text(strip=True) if name_link else item_cell.get_text(strip=True)
        if not name:
            continue

        items.append(GameItem(
            name=name,
            rarity=get_cell("rarity").get_text(strip=True) if get_cell("rarity") else "",
            recycles_to=_parse_recycles_to(get_cell("recycles_to")),
            sell_price=_parse_int_cell(get_cell("sell_price")),
            stack_size=_parse_int_cell(get_cell("stack_size")),
            category=get_cell("category").get_text(strip=True) if get_cell("category") else "",
            uses=_parse_uses(get_cell("uses")),
        ))

    print(f"  Scraped {len(items)} items from wiki")
    return items


# =============================================================================
# Action generation (shared by both sources)
# =============================================================================

def generate_action(item: GameItem) -> tuple[str, str, str]:
    """Generate (action, recycle_for, keep_for) based on item data."""
    has_uses = bool(item.uses.strip())
    can_recycle = bool(item.recycles_to.strip())
    category = item.category.strip()

    keep_for = item.uses if has_uses else ""
    recycle_for = item.recycles_to

    if category == "Key":
        return "Keep", "", "Unlocks locked areas"
    if category == "Quick Use":
        if has_uses:
            return "Keep", recycle_for, keep_for
        return "Use", recycle_for, "Quick Use consumable"
    if category == "Ammunition":
        return "Keep", "", "Ammo"
    if category in {"Shield", "Augment", "Mods"}:
        return "Keep", recycle_for, f"Equippable {category}"
    if category in MATERIAL_CATEGORIES:
        if has_uses:
            return "Keep", recycle_for, keep_for
        return "Keep", recycle_for, f"{category} - crafting ingredient"
    if category == "Nature":
        if has_uses:
            return "Keep", recycle_for, keep_for
        return "Sell", recycle_for, ""
    if category == "Trinket":
        if has_uses:
            return "Keep until uses complete; sell after", "", keep_for
        return "Sell", "", ""
    if category == "Recyclable":
        if has_uses:
            return "Keep until uses complete; recycle after", recycle_for, keep_for
        if can_recycle:
            if item.sell_price >= 2000:
                return "Recycle if short on materials; Sell otherwise", recycle_for, ""
            return "Recycle", recycle_for, ""
        return "Sell", "", ""

    if has_uses:
        return "Keep", recycle_for, keep_for
    if can_recycle:
        return "Recycle", recycle_for, ""
    return "Sell", "", ""


# =============================================================================
# CSV / DB operations
# =============================================================================

def load_existing_csv(csv_path: Path) -> dict[str, dict[str, str]]:
    existing: dict[str, dict[str, str]] = {}
    if not csv_path.exists():
        return existing
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("name", "").strip()
            if name:
                existing[name.lower()] = {
                    "name": name,
                    "action": row.get("action", "").strip(),
                    "recycle_for": row.get("recycle_for", "").strip(),
                    "keep_for": row.get("keep_for", "").strip(),
                    "sell_price": row.get("sell_price", "").strip(),
                    "stack_size": row.get("stack_size", "").strip(),
                }
    return existing


def build_csv_rows(
    items: list[GameItem],
    existing: dict[str, dict[str, str]] | None = None,
    merge: bool = False,
) -> tuple[list[dict[str, str]], ScraperStats]:
    stats = ScraperStats(items_scraped=len(items))
    rows: list[dict[str, str]] = []

    for item in items:
        action, recycle_for, keep_for = generate_action(item)
        item.action = action
        item.recycle_for = recycle_for
        item.keep_for = keep_for

        key = item.name.lower()
        sell_price = str(item.sell_price) if item.sell_price else ""
        stack_size = str(item.stack_size) if item.stack_size else ""

        if merge and existing and key in existing:
            old = existing[key]
            rows.append({
                "name": item.name,
                "action": old["action"],
                "recycle_for": old["recycle_for"] or recycle_for,
                "keep_for": old["keep_for"] or keep_for,
                "sell_price": sell_price,
                "stack_size": stack_size,
            })
            stats.items_preserved += 1
        else:
            rows.append({
                "name": item.name,
                "action": action,
                "recycle_for": recycle_for,
                "keep_for": keep_for,
                "sell_price": sell_price,
                "stack_size": stack_size,
            })
            if existing and key not in (existing or {}):
                stats.items_new += 1

    rows.sort(key=lambda r: r["name"].lower())
    stats.items_written = len(rows)
    return rows, stats


def write_csv(rows: list[dict[str, str]], csv_path: Path) -> None:
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["name", "action", "recycle_for", "keep_for", "sell_price", "stack_size"],
        )
        writer.writeheader()
        writer.writerows(rows)


def rebuild_database(csv_path: Path, db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS items")
        conn.execute("""
            CREATE TABLE items (
                name TEXT PRIMARY KEY NOT NULL COLLATE NOCASE,
                action TEXT NOT NULL,
                recycle_for TEXT,
                keep_for TEXT,
                sell_price INTEGER,
                stack_size INTEGER
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_name ON items(name COLLATE NOCASE)")

        count = 0
        with csv_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("name", "").strip()
                action = row.get("action", "").strip()
                recycle_for = row.get("recycle_for", "").strip() or None
                keep_for = row.get("keep_for", "").strip() or None
                sell_price_str = row.get("sell_price", "").strip()
                stack_size_str = row.get("stack_size", "").strip()
                sell_price = int(sell_price_str) if sell_price_str else None
                stack_size = int(stack_size_str) if stack_size_str else None

                if not name or not action:
                    continue

                conn.execute(
                    """
                    INSERT INTO items (name, action, recycle_for, keep_for, sell_price, stack_size)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        action = excluded.action,
                        recycle_for = excluded.recycle_for,
                        keep_for = excluded.keep_for,
                        sell_price = excluded.sell_price,
                        stack_size = excluded.stack_size
                    """,
                    (name, action, recycle_for, keep_for, sell_price, stack_size),
                )
                count += 1

        conn.commit()
        return count
    finally:
        conn.close()


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update items.csv and items.db from MetaForge API or Arc Raiders Wiki.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python update_db.py               # Full update from MetaForge API
  python update_db.py --merge       # Update but keep existing manual overrides
  python update_db.py --dry-run     # Preview changes without writing
  python update_db.py --source wiki # Force wiki scraper instead of API
        """,
    )
    parser.add_argument("--merge", action="store_true",
                        help="Preserve existing action overrides from items.csv")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without writing any files")
    parser.add_argument("--csv-only", action="store_true",
                        help="Only update items.csv, do not rebuild items.db")
    parser.add_argument("--source", choices=["api", "wiki"], default="api",
                        help="Data source: 'api' for MetaForge (default), 'wiki' for Arc Raiders Wiki")

    args = parser.parse_args()

    if args.source == "wiki":
        items = fetch_items_from_wiki()
        source_name = "Arc Raiders Wiki"
    else:
        try:
            items = fetch_items_from_metaforge()
            source_name = "MetaForge API"
        except Exception as e:
            print(f"\nMetaForge API failed ({e}), falling back to wiki...")
            items = fetch_items_from_wiki()
            source_name = "Arc Raiders Wiki (fallback)"

    if not items:
        print("No items found. Aborting.")
        sys.exit(1)

    existing = None
    if args.merge:
        existing = load_existing_csv(ITEMS_CSV)
        print(f"  Loaded {len(existing)} existing items for merge")

    rows, stats = build_csv_rows(items, existing, merge=args.merge)
    stats.source = source_name

    print(f"\n{'=' * 50}")
    print(f"  Source:                  {stats.source}")
    print(f"  Items fetched:           {stats.items_scraped}")
    print(f"  Items to write:          {stats.items_written}")
    if args.merge:
        print(f"  Existing preserved:      {stats.items_preserved}")
        print(f"  New items added:         {stats.items_new}")
    print(f"{'=' * 50}")

    if args.dry_run:
        print("\n[DRY RUN] No files written. Preview of first 20 items:\n")
        print(f"  {'Name':<35} {'Action':<40} {'Sell':>7} {'Stack':>5}  {'Recycle For':<25}")
        print(f"  {'-'*35} {'-'*40} {'-'*7} {'-'*5}  {'-'*25}")
        for row in rows[:20]:
            sell = row.get("sell_price", "")
            stack = row.get("stack_size", "")
            print(f"  {row['name']:<35} {row['action']:<40} {sell:>7} {stack:>5}  {row['recycle_for']:<25}")
        print(f"\n  ... and {len(rows) - 20} more items")
        return

    write_csv(rows, ITEMS_CSV)
    print(f"\n  Wrote {len(rows)} items to {ITEMS_CSV}")

    if not args.csv_only:
        db_count = rebuild_database(ITEMS_CSV, ITEMS_DB)
        print(f"  Rebuilt {ITEMS_DB} with {db_count} items")

    print("\nDone!")


# =============================================================================
# Auto-update: called by the main app on startup
# =============================================================================

LAST_UPDATE_FILE = SCRIPT_DIR / ".last_wiki_update"
AUTO_UPDATE_INTERVAL_HOURS = 24


def _should_auto_update() -> bool:
    if not LAST_UPDATE_FILE.exists():
        return True
    try:
        last_ts = float(LAST_UPDATE_FILE.read_text(encoding="utf-8").strip())
        hours_since = (time.time() - last_ts) / 3600
        return hours_since >= AUTO_UPDATE_INTERVAL_HOURS
    except (ValueError, OSError):
        return True


def _mark_updated() -> None:
    try:
        LAST_UPDATE_FILE.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def auto_update(*, force: bool = False) -> bool:
    """
    Run a database update if due. Called by the main app on startup.

    Uses MetaForge API with wiki fallback, always in merge mode.
    Throttled to once per 24 hours unless force=True.
    Fails silently so the app always starts.
    """
    if not force and not _should_auto_update():
        return False

    try:
        print("Checking MetaForge for item database updates...")
        try:
            items = fetch_items_from_metaforge()
        except Exception:
            print("  MetaForge API unavailable, trying wiki...")
            try:
                items = fetch_items_from_wiki()
            except Exception:
                print("  Wiki also unavailable, skipping update.")
                return False

        if not items:
            print("  No items found, skipping update.")
            return False

        existing = load_existing_csv(ITEMS_CSV)
        rows, stats = build_csv_rows(items, existing, merge=True)

        write_csv(rows, ITEMS_CSV)
        rebuild_database(ITEMS_CSV, ITEMS_DB)
        _mark_updated()

        new_count = stats.items_written - stats.items_preserved
        print(
            f"  Item database updated: {stats.items_written} items "
            f"({new_count} new, {stats.items_preserved} preserved)"
        )
        return True

    except Exception as e:  # noqa: BLE001
        print(f"  Database update skipped (error: {e})")
        return False


if __name__ == "__main__":
    main()
