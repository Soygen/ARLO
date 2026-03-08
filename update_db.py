#!/usr/bin/env python3
"""
Wiki Scraper & Database Updater for PabsArcTooltip.

Scrapes the Arc Raiders Wiki loot table and updates items.csv + items.db
with auto-generated action recommendations.

Usage:
    python update_db.py                 # Full update (overwrites items.csv)
    python update_db.py --merge         # Merge: keep existing manual overrides
    python update_db.py --dry-run       # Preview without writing files
    python update_db.py --csv-only      # Only update items.csv, skip DB rebuild
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
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


WIKI_URL = "https://arcraiders.wiki/wiki/Loot"
SCRIPT_DIR = Path(__file__).parent.resolve()
ITEMS_CSV = SCRIPT_DIR / "items.csv"
ITEMS_DB = SCRIPT_DIR / "items.db"

# Categories that are generally "use in the field" items, not inventory loot
QUICK_USE_CATEGORIES = {"Quick Use", "Shield", "Augment", "Mods", "Ammunition", "Key"}

# Categories where the item is a crafting ingredient you should keep
MATERIAL_CATEGORIES = {"Basic Material", "Refined Material", "Topside Material"}


@dataclass
class WikiItem:
    """An item parsed from the wiki loot table."""

    name: str
    rarity: str = ""
    recycles_to: str = ""
    sell_price: int = 0
    stack_size: int = 0
    category: str = ""
    uses: str = ""

    # Derived fields for the existing DB schema
    action: str = ""
    recycle_for: str = ""
    keep_for: str = ""


@dataclass
class ScraperStats:
    """Track scraper results."""

    items_scraped: int = 0
    items_written: int = 0
    items_preserved: int = 0
    items_new: int = 0
    errors: list[str] = field(default_factory=list)


def fetch_wiki_page() -> str:
    """Fetch the loot page HTML from the wiki."""
    print(f"Fetching {WIKI_URL} ...")
    resp = requests.get(WIKI_URL, timeout=30, headers={
        "User-Agent": "PabsArcTooltip-Updater/1.0 (item database sync)"
    })
    resp.raise_for_status()
    print(f"  Got {len(resp.text):,} bytes")
    return resp.text


def parse_recycles_to(cell) -> str:
    """
    Parse a 'Recycles To' cell into a clean string like '2x Metal Parts, 4x Wires'.

    The wiki uses format like: '4× Metal Parts 6× Wires' with links.
    """
    if not cell:
        return ""

    text = cell.get_text(" ", strip=True)

    # "Cannot be recycled" → empty
    if "cannot" in text.lower() or "n/a" in text.lower():
        return ""

    # Normalize × to x and clean up spacing
    text = text.replace("×", "x")

    # Split on the pattern: number followed by 'x' to find each component
    # e.g. "4x Metal Parts 6x Wires" → ["4x Metal Parts", "6x Wires"]
    parts = re.split(r"(?<=\S)\s+(?=\d+x\s)", text)
    parts = [p.strip() for p in parts if p.strip()]

    return ", ".join(parts)


def parse_uses(cell) -> str:
    """
    Parse a 'Uses' cell into a clean string.

    Wiki format is like:
        **Workshop** Gear Bench 3 (5×) Utility Station 3 (5×)
        **Projects** Expedition 1 (5×)
        **Quests** Doctor's Orders (2×)
    """
    if not cell:
        return ""

    text = cell.get_text(" ", strip=True)
    if not text:
        return ""

    # Normalize × to x
    text = text.replace("×", "x")

    # Clean up excessive whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def parse_sell_price(cell) -> int:
    """Parse sell price, stripping commas."""
    if not cell:
        return 0
    text = cell.get_text(strip=True).replace(",", "").strip()
    try:
        return int(text)
    except ValueError:
        return 0


def parse_stack_size(cell) -> int:
    """Parse stack size."""
    if not cell:
        return 0
    text = cell.get_text(strip=True).strip()
    try:
        return int(text)
    except ValueError:
        return 0


def scrape_items(html: str) -> list[WikiItem]:
    """Parse the wiki HTML and extract all items from the loot table."""
    soup = BeautifulSoup(html, "html.parser")

    # Find the loot table by looking for a table with the right headers
    loot_table = None
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if "item" in headers and "rarity" in headers and "recycles to" in headers:
            loot_table = table
            break

    if not loot_table:
        print("ERROR: Could not find the loot table on the wiki page!")
        print("  The wiki page structure may have changed.")
        sys.exit(1)

    # Determine column indices from headers
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

    items: list[WikiItem] = []
    rows = loot_table.find_all("tr")[1:]  # skip header row

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        def get_cell(key: str):
            idx = col_map.get(key)
            if idx is not None and idx < len(cells):
                return cells[idx]
            return None

        # Extract item name from the link text in the Item column
        item_cell = get_cell("item")
        if not item_cell:
            continue

        name_link = item_cell.find("a")
        name = name_link.get_text(strip=True) if name_link else item_cell.get_text(strip=True)
        if not name:
            continue

        item = WikiItem(
            name=name,
            rarity=get_cell("rarity").get_text(strip=True) if get_cell("rarity") else "",
            recycles_to=parse_recycles_to(get_cell("recycles_to")),
            sell_price=parse_sell_price(get_cell("sell_price")),
            stack_size=parse_stack_size(get_cell("stack_size")),
            category=get_cell("category").get_text(strip=True) if get_cell("category") else "",
            uses=parse_uses(get_cell("uses")),
        )
        items.append(item)

    print(f"  Scraped {len(items)} items from wiki")
    return items


def generate_action(item: WikiItem) -> tuple[str, str, str]:
    """
    Generate (action, recycle_for, keep_for) based on wiki data.

    Returns a tuple of (action, recycle_for, keep_for) strings matching
    the existing database schema.
    """
    has_uses = bool(item.uses.strip())
    can_recycle = bool(item.recycles_to.strip())
    category = item.category.strip()

    # --- Build keep_for from uses ---
    keep_for = item.uses if has_uses else ""

    # --- Build recycle_for ---
    recycle_for = item.recycles_to

    # --- Determine action ---

    # Keys: always keep
    if category == "Key":
        return "Keep", "", "Unlocks locked areas"

    # Quick Use items: these are consumables you use in the field
    if category == "Quick Use":
        if has_uses:
            return "Keep", recycle_for, keep_for
        return "Use", recycle_for, "Quick Use consumable"

    # Ammunition
    if category == "Ammunition":
        return "Keep", "", "Ammo"

    # Shields, Augments, Mods: keep (equippable gear)
    if category in {"Shield", "Augment", "Mods"}:
        return "Keep", recycle_for, f"Equippable {category}"

    # Basic/Refined/Topside Materials: keep (crafting ingredients)
    if category in MATERIAL_CATEGORIES:
        if has_uses:
            return "Keep", recycle_for, keep_for
        # Materials without specific listed uses are still generally useful
        return "Keep", recycle_for, f"{category} - crafting ingredient"

    # Nature items
    if category == "Nature":
        if has_uses:
            return "Keep", recycle_for, keep_for
        # Nature items without uses: sell (like Agave, Roots)
        if item.sell_price >= 800:
            return "Sell", recycle_for, ""
        return "Sell", recycle_for, ""

    # Trinkets: sell (they exist to be sold for credits)
    if category == "Trinket":
        if has_uses:
            # Some trinkets have project uses (e.g. Breathtaking Snow Globe)
            return "Keep until uses complete; sell after", "", keep_for
        return "Sell", "", ""

    # Recyclables: the big category of junk items
    if category == "Recyclable":
        if has_uses:
            # Has workshop/quest/project uses → keep until done
            return "Keep until uses complete; recycle after", recycle_for, keep_for
        # No uses: recycle for materials or sell if high value
        if can_recycle:
            if item.sell_price >= 2000:
                return "Recycle if short on materials; Sell otherwise", recycle_for, ""
            return "Recycle", recycle_for, ""
        # Can't recycle and no uses → sell
        return "Sell", "", ""

    # Fallback: if we don't recognize the category
    if has_uses:
        return "Keep", recycle_for, keep_for
    if can_recycle:
        return "Recycle", recycle_for, ""
    return "Sell", "", ""


def load_existing_csv(csv_path: Path) -> dict[str, dict[str, str]]:
    """Load existing items.csv into a dict keyed by lowercase item name."""
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
    wiki_items: list[WikiItem],
    existing: dict[str, dict[str, str]] | None = None,
    merge: bool = False,
) -> tuple[list[dict[str, str]], ScraperStats]:
    """
    Build the final CSV rows from scraped wiki items.

    If merge=True and existing data is provided, existing manual overrides
    are preserved for items that already exist in the CSV.
    """
    stats = ScraperStats(items_scraped=len(wiki_items))
    rows: list[dict[str, str]] = []

    for item in wiki_items:
        action, recycle_for, keep_for = generate_action(item)
        item.action = action
        item.recycle_for = recycle_for
        item.keep_for = keep_for

        key = item.name.lower()
        sell_price = str(item.sell_price) if item.sell_price else ""
        stack_size = str(item.stack_size) if item.stack_size else ""

        if merge and existing and key in existing:
            # Preserve the existing manual override
            old = existing[key]
            rows.append({
                "name": item.name,  # Use wiki's canonical name
                "action": old["action"],
                "recycle_for": old["recycle_for"] or recycle_for,  # Fill in if was empty
                "keep_for": old["keep_for"] or keep_for,
                "sell_price": sell_price,  # Always use latest wiki price
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

    # Sort alphabetically
    rows.sort(key=lambda r: r["name"].lower())
    stats.items_written = len(rows)
    return rows, stats


def write_csv(rows: list[dict[str, str]], csv_path: Path) -> None:
    """Write rows to CSV file."""
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["name", "action", "recycle_for", "keep_for", "sell_price", "stack_size"],
        )
        writer.writeheader()
        writer.writerows(rows)


def rebuild_database(csv_path: Path, db_path: Path) -> int:
    """Rebuild the SQLite database from the CSV file."""
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update items.csv and items.db from the Arc Raiders Wiki.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python update_db.py               # Full update from wiki
  python update_db.py --merge       # Update but keep existing manual overrides
  python update_db.py --dry-run     # Preview changes without writing
  python update_db.py --csv-only    # Update CSV only, skip DB rebuild
        """,
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Preserve existing action overrides from items.csv for known items",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without writing any files",
    )
    parser.add_argument(
        "--csv-only",
        action="store_true",
        help="Only update items.csv, do not rebuild items.db",
    )
    parser.add_argument(
        "--url",
        default=WIKI_URL,
        help=f"Wiki loot page URL (default: {WIKI_URL})",
    )

    args = parser.parse_args()

    # 1. Fetch wiki page
    html = fetch_wiki_page()

    # 2. Parse items
    wiki_items = scrape_items(html)
    if not wiki_items:
        print("No items found. Aborting.")
        sys.exit(1)

    # 3. Load existing CSV if merging
    existing = None
    if args.merge:
        existing = load_existing_csv(ITEMS_CSV)
        print(f"  Loaded {len(existing)} existing items for merge")

    # 4. Build final rows
    rows, stats = build_csv_rows(wiki_items, existing, merge=args.merge)

    # 5. Report
    print(f"\n{'=' * 50}")
    print(f"  Items scraped from wiki: {stats.items_scraped}")
    print(f"  Items to write:          {stats.items_written}")
    if args.merge:
        print(f"  Existing preserved:      {stats.items_preserved}")
        print(f"  New items added:         {stats.items_new}")
    print(f"{'=' * 50}")

    if args.dry_run:
        print("\n[DRY RUN] No files written. Here's a preview of the first 20 items:\n")
        print(f"  {'Name':<35} {'Action':<40} {'Sell':>7} {'Stack':>5}  {'Recycle For':<25}")
        print(f"  {'-'*35} {'-'*40} {'-'*7} {'-'*5}  {'-'*25}")
        for row in rows[:20]:
            sell = row.get('sell_price', '')
            stack = row.get('stack_size', '')
            print(f"  {row['name']:<35} {row['action']:<40} {sell:>7} {stack:>5}  {row['recycle_for']:<25}")
        print(f"\n  ... and {len(rows) - 20} more items")
        return

    # 6. Write CSV
    write_csv(rows, ITEMS_CSV)
    print(f"\n  Wrote {len(rows)} items to {ITEMS_CSV}")

    # 7. Rebuild DB
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
    """Check if enough time has passed since the last auto-update."""
    import time

    if not LAST_UPDATE_FILE.exists():
        return True

    try:
        last_ts = float(LAST_UPDATE_FILE.read_text(encoding="utf-8").strip())
        hours_since = (time.time() - last_ts) / 3600
        return hours_since >= AUTO_UPDATE_INTERVAL_HOURS
    except (ValueError, OSError):
        return True


def _mark_updated() -> None:
    """Write the current timestamp to the update marker file."""
    import time

    try:
        LAST_UPDATE_FILE.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass  # Non-critical, just means we'll update again next launch


def auto_update(*, force: bool = False) -> bool:
    """
    Run a wiki database update if due. Called by the main app on startup.

    Uses merge mode to preserve any manual action overrides.
    Throttled to once per 24 hours unless force=True.
    Fails silently on any error so the app always starts.

    Returns True if an update was performed, False otherwise.
    """
    if not force and not _should_auto_update():
        return False

    try:
        print("Checking wiki for item database updates...")
        html = fetch_wiki_page()
        wiki_items = scrape_items(html)

        if not wiki_items:
            print("  No items found on wiki, skipping update.")
            return False

        existing = load_existing_csv(ITEMS_CSV)
        rows, stats = build_csv_rows(wiki_items, existing, merge=True)

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
        print(f"  Wiki update skipped (offline or error: {e})")
        return False


if __name__ == "__main__":
    main()
