#!/usr/bin/env python3
"""
Hybrid Database Updater for ARLO (Arc Raiders Loot Overlay).

Combines data from two sources for best results:
  - MetaForge API: items, sell prices, stack sizes, recycle components, quests
  - Arc Raiders Wiki: workshop upgrades, expedition requirements, project uses

Usage:
    python update_db.py                 # Full update (overwrites items.csv)
    python update_db.py --merge         # Merge: keep existing manual overrides
    python update_db.py --dry-run       # Preview without writing files
    python update_db.py --csv-only      # Only update items.csv, skip DB rebuild
    python update_db.py --source wiki   # Force wiki-only mode
    python update_db.py --source api    # Force MetaForge-only mode (no wiki)
"""

from __future__ import annotations

import argparse
import csv
import html
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
METAFORGE_SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVuaGJ2a3N6d2hjemJqeGdldGdrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDQ5NjgwMjUsImV4cCI6MjA2MDU0NDAyNX0."
    "gckCmxnlpwwJOGmc5ebLYDnaWaxr5PW31eCrSPR5aRQ"
)

WIKI_URL = "https://arcraiders.wiki/wiki/"
WIKI_LOOT_PATH = "Loot"
WIKI_QUESTS_PATH = "Quests"
WIKI_WORKSHOP_PATH = "Workshop"
WIKI_PROJECTS_PATH = "Projects"
WIKI_TRADERS_PATH = "Traders"
SCRIPT_DIR = Path(__file__).parent.resolve()
DATA_DIR = SCRIPT_DIR / "data"
ITEMS_CSV = DATA_DIR / "items.csv"
QUESTS_CSV = DATA_DIR / "quests.csv"
PROJECTS_CSV = DATA_DIR / "projects.csv"
TRADERS_CSV = DATA_DIR / "traders.csv"
# Single SQLite catalog for items + quests + projects + traders
ARLO_DB = SCRIPT_DIR / "arlo.db"
# Back-compat aliases (same file)
ITEMS_DB = ARLO_DB
QUESTS_DB = ARLO_DB
PROJECTS_DB = ARLO_DB
TRADERS_DB = ARLO_DB

USER_AGENT = "ARLO/1.0 (Arc Raiders Loot Overlay - https://github.com/Soygen/ARLO)"

QUICK_USE_CATEGORIES = {"Quick Use", "Shield", "Augment", "Mods", "Ammunition", "Key"}
MATERIAL_CATEGORIES = {"Basic Material", "Refined Material", "Topside Material"}


# =============================================================================
# Data classes
# =============================================================================

@dataclass
class GameItem:
    """An item normalized to a common format."""

    name: str
    rarity: str = ""
    recycles_to: str = ""
    sell_price: int = 0
    stack_size: int = 0
    category: str = ""
    uses: str = ""  # Workshop/project/expedition uses (from wiki)
    quest_uses: str = ""  # Quest requirements (from MetaForge)

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
# MetaForge API
# =============================================================================

def _api_get(url: str, *, timeout: int = 30):
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.json()


def _supabase_get(table: str, params: str = "") -> list:
    url = f"{METAFORGE_SUPABASE_URL}/{table}"
    if params:
        url += f"?{params}"
    resp = requests.get(
        url, timeout=30,
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

    while True:
        data = _api_get(f"{METAFORGE_API_BASE}/items?page={page}&limit=100")
        items = data.get("data", [])
        pagination = data.get("pagination", {})
        all_items.extend(items)
        total = pagination.get("total", "?")
        print(f"  Page {page}: {len(items)} items (total: {len(all_items)}/{total})")

        if not pagination.get("hasNextPage", False):
            break
        page += 1
        time.sleep(0.1)

    print(f"  Fetched {len(all_items)} items")
    return all_items


def fetch_wiki_recycle_map() -> dict[str, str]:
    """Scrape the wiki loot table and return item_name -> recycles_to string map."""
    print("  Falling back to wiki for recycle data...")
    try:
        html = fetch_wiki_page()
    except Exception as e:
        print(f"  Wiki recycle fallback also failed ({e})")
        return {}

    soup = BeautifulSoup(html, "html.parser")
    loot_table = None
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if "item" in headers and "recycles to" in headers:
            loot_table = table
            break

    if not loot_table:
        print("  Could not find recycle table on wiki")
        return {}

    header_row = loot_table.find("tr")
    headers = [th.get_text(strip=True).lower() for th in header_row.find_all("th")]
    item_idx = next((i for i, h in enumerate(headers) if h == "item"), None)
    recycle_idx = next((i for i, h in enumerate(headers) if "recycles" in h), None)

    if item_idx is None or recycle_idx is None:
        return {}

    recycle_map: dict[str, str] = {}
    for row in loot_table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) <= max(item_idx, recycle_idx):
            continue
        item_cell = cells[item_idx]
        name_link = item_cell.find("a")
        name = name_link.get_text(strip=True) if name_link else item_cell.get_text(strip=True)
        if not name:
            continue
        recycles_to = _parse_recycles_to(cells[recycle_idx])
        if recycles_to:
            recycle_map[name] = recycles_to

    print(f"  Loaded recycle data for {len(recycle_map)} items from wiki")
    return recycle_map


def fetch_metaforge_recycle_map(all_items: list[dict]) -> dict[str, str]:
    """Fetch recycle components and build item_name -> '2x Metal Parts, 3x Wires' map."""
    print("Fetching recycle data from MetaForge...")
    try:
        components = _supabase_get("arc_item_recycle_components", "select=*")
    except Exception as e:
        print(f"  Supabase unavailable ({e}), falling back to wiki for recycle data...")
        return fetch_wiki_recycle_map()

    id_to_name = {item["id"]: item["name"] for item in all_items if item.get("id") and item.get("name")}

    recycle_raw: dict[str, list[tuple[str, int]]] = {}
    for comp in components:
        item_id, comp_id, qty = comp.get("item_id", ""), comp.get("component_id", ""), comp.get("quantity", 0)
        if item_id and comp_id:
            recycle_raw.setdefault(item_id, []).append((comp_id, qty))

    recycle_map: dict[str, str] = {}
    for item_id, parts in recycle_raw.items():
        item_name = id_to_name.get(item_id, "")
        if item_name:
            recycle_map[item_name] = ", ".join(f"{qty}x {id_to_name.get(cid, cid)}" for cid, qty in parts)

    print(f"  Loaded recycle data for {len(recycle_map)} items")
    return recycle_map


def fetch_metaforge_quests() -> list[dict]:
    """Fetch the full MetaForge quest catalog (all pages)."""
    print("Fetching quests from MetaForge API...")
    all_quests: list[dict] = []
    page = 1

    while True:
        try:
            data = _api_get(f"{METAFORGE_API_BASE}/quests?page={page}&limit=100")
        except Exception as e:
            print(f"  Quest data unavailable ({e})")
            return []

        quests = data.get("data", [])
        pagination = data.get("pagination", {})
        all_quests.extend(quests)

        if not pagination.get("hasNextPage", False):
            break
        page += 1
        time.sleep(0.1)

    print(f"  Fetched {len(all_quests)} quests")
    return all_quests


def fetch_metaforge_quest_tree() -> dict:
    """Fetch quest-tree parentRelationships (best-effort)."""
    try:
        data = _api_get(f"{METAFORGE_API_BASE}/quest-tree")
    except Exception as e:
        print(f"  Quest tree unavailable ({e})")
        return {}
    return data.get("data") or {}


def _quest_prereq_ids(quest: dict, tree_parents: dict[str, list[str]]) -> list[str]:
    """Collect parent quest ids from manifest + quest-tree."""
    found: list[str] = []
    seen: set[str] = set()
    for src in (
        (quest.get("manifest") or {}).get("prerequisites") or [],
    ):
        if not isinstance(src, dict):
            continue
        qid = str(src.get("quest_id") or "").strip()
        if qid and qid not in seen:
            seen.add(qid)
            found.append(qid)
    for qid in tree_parents.get(str(quest.get("id") or ""), []):
        if qid and qid not in seen:
            seen.add(qid)
            found.append(qid)
    return found


def _quest_requirement_rows(quest: dict) -> list[dict[str, str]]:
    """Normalize required_items into CSV/DB rows."""
    rows: list[dict[str, str]] = []
    quest_id = str(quest.get("id") or "").strip()
    quest_name = str(quest.get("name") or "").strip()
    for req in quest.get("required_items") or []:
        if not isinstance(req, dict):
            continue
        item = req.get("item") if isinstance(req.get("item"), dict) else {}
        item_id = str(req.get("item_id") or item.get("id") or "").strip()
        item_name = str(item.get("name") or req.get("name") or "").strip()
        qty = req.get("quantity", req.get("count", 1))
        try:
            quantity = int(qty)
        except (TypeError, ValueError):
            quantity = 1
        if not item_id and not item_name:
            continue
        rows.append({
            "quest_id": quest_id,
            "quest_name": quest_name,
            "item_id": item_id or item_name,
            "item_name": item_name or item_id,
            "quantity": str(quantity),
        })
    return rows


def build_quest_catalog_rows(
    quests: list[dict],
    tree: dict | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Build quest / requirement / prerequisite CSV rows from API payloads."""
    tree_parents: dict[str, list[str]] = {}
    for rel in (tree or {}).get("parentRelationships") or []:
        if not isinstance(rel, dict):
            continue
        child = str(rel.get("quest_id") or "").strip()
        parent = str(rel.get("parent_quest_id") or "").strip()
        if child and parent:
            tree_parents.setdefault(child, []).append(parent)

    quest_rows: list[dict[str, str]] = []
    req_rows: list[dict[str, str]] = []
    prereq_rows: list[dict[str, str]] = []

    for quest in quests:
        qid = str(quest.get("id") or "").strip()
        name = str(quest.get("name") or "").strip()
        if not qid or not name:
            continue
        prereqs = _quest_prereq_ids(quest, tree_parents)
        quest_rows.append({
            "id": qid,
            "name": name,
            "trader_name": str(quest.get("trader_name") or "").strip(),
            "xp": str(int(quest.get("xp") or 0)),
            "prerequisites": ";".join(prereqs),
        })
        for parent in prereqs:
            prereq_rows.append({
                "quest_id": qid,
                "parent_quest_id": parent,
                "required_state": "COMPLETED",
            })
        req_rows.extend(_quest_requirement_rows(quest))

    quest_rows.sort(key=lambda r: r["id"])
    req_rows.sort(key=lambda r: (r["quest_id"], r["item_id"]))
    prereq_rows.sort(key=lambda r: (r["quest_id"], r["parent_quest_id"]))
    return quest_rows, req_rows, prereq_rows


def quest_map_from_requirements(
    req_rows: list[dict[str, str]],
) -> dict[str, list[str]]:
    """item_name_lower -> [\"Quest Name (Nx)\", ...] for keep_for generation."""
    quest_map: dict[str, list[str]] = {}
    for row in req_rows:
        iname = (row.get("item_name") or "").strip()
        qname = (row.get("quest_name") or "").strip()
        qty = (row.get("quantity") or "1").strip()
        if iname and qname:
            quest_map.setdefault(iname.lower(), []).append(f"{qname} ({qty}x)")
    return quest_map


def write_quests_csv(
    quest_rows: list[dict[str, str]],
    req_rows: list[dict[str, str]],
    csv_path: Path,
) -> None:
    """Write one-row-per-quest CSV with encoded requirements."""
    req_by_quest: dict[str, list[dict[str, str]]] = {}
    for row in req_rows:
        req_by_quest.setdefault(row["quest_id"], []).append(row)

    out_rows: list[dict[str, str]] = []
    for q in quest_rows:
        parts: list[str] = []
        for req in req_by_quest.get(q["id"], []):
            # item_id:qty:item_name  (name may contain spaces; no pipes)
            parts.append(
                f"{req['item_id']}:{req['quantity']}:{req['item_name']}"
            )
        out_rows.append({
            "id": q["id"],
            "name": q["name"],
            "trader_name": q["trader_name"],
            "xp": q["xp"],
            "prerequisites": q["prerequisites"],
            "required_items": "|".join(parts),
        })

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "name",
                "trader_name",
                "xp",
                "prerequisites",
                "required_items",
            ],
        )
        writer.writeheader()
        writer.writerows(out_rows)


def rebuild_quests_database(
    quest_rows: list[dict[str, str]],
    req_rows: list[dict[str, str]],
    prereq_rows: list[dict[str, str]],
    db_path: Path,
) -> int:
    """Rebuild quest tables in the shared catalog DB."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            DROP TABLE IF EXISTS quest_requirements;
            DROP TABLE IF EXISTS quest_prerequisites;
            DROP TABLE IF EXISTS quests;
            CREATE TABLE quests (
                id TEXT PRIMARY KEY NOT NULL,
                name TEXT NOT NULL,
                trader_name TEXT,
                xp INTEGER
            );
            CREATE TABLE quest_requirements (
                quest_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                item_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                PRIMARY KEY (quest_id, item_id),
                FOREIGN KEY (quest_id) REFERENCES quests(id)
            );
            CREATE TABLE quest_prerequisites (
                quest_id TEXT NOT NULL,
                parent_quest_id TEXT NOT NULL,
                required_state TEXT NOT NULL DEFAULT 'COMPLETED',
                PRIMARY KEY (quest_id, parent_quest_id),
                FOREIGN KEY (quest_id) REFERENCES quests(id)
            );
            CREATE INDEX idx_quest_req_item_name
                ON quest_requirements(item_name COLLATE NOCASE);
            CREATE INDEX idx_quest_req_item_id
                ON quest_requirements(item_id);
            """
        )
        conn.executemany(
            "INSERT INTO quests (id, name, trader_name, xp) VALUES (?, ?, ?, ?)",
            [
                (r["id"], r["name"], r["trader_name"] or None, int(r["xp"] or 0))
                for r in quest_rows
            ],
        )
        conn.executemany(
            "INSERT INTO quest_requirements "
            "(quest_id, item_id, item_name, quantity) VALUES (?, ?, ?, ?)",
            [
                (
                    r["quest_id"],
                    r["item_id"],
                    r["item_name"],
                    int(r["quantity"]),
                )
                for r in req_rows
            ],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO quest_prerequisites "
            "(quest_id, parent_quest_id, required_state) VALUES (?, ?, ?)",
            [
                (r["quest_id"], r["parent_quest_id"], r["required_state"])
                for r in prereq_rows
            ],
        )
        conn.commit()
        return len(quest_rows)
    finally:
        conn.close()


def write_quest_catalog(
    quests: list[dict],
    *,
    tree: dict | None = None,
    csv_path: Path = QUESTS_CSV,
    db_path: Path = QUESTS_DB,
) -> dict[str, list[str]]:
    """Persist quest catalog and return item→quest keep_for map."""
    if not quests:
        return {}
    if tree is None:
        tree = fetch_metaforge_quest_tree()
    quest_rows, req_rows, prereq_rows = build_quest_catalog_rows(quests, tree)
    write_quests_csv(quest_rows, req_rows, csv_path)
    count = rebuild_quests_database(quest_rows, req_rows, prereq_rows, db_path)
    print(f"  Wrote {count} quests to {csv_path.name} / {db_path.name}")
    print(f"  ({len(req_rows)} item requirements, {len(prereq_rows)} prerequisites)")
    return quest_map_from_requirements(req_rows)


def fetch_quest_catalog_data() -> tuple[list[dict], dict | None]:
    """Load quests from MetaForge, falling back to / enriching from the wiki.

    Returns ``(quests, tree_or_none)``. Tree (prerequisites) only comes from
    MetaForge; wiki fallback has empty prerequisites.
    """
    quests = fetch_metaforge_quests()
    tree: dict | None = fetch_metaforge_quest_tree() if quests else None

    wiki_quests: list[dict] = []
    try:
        wiki_quests = scrape_wiki_quests()
    except Exception as e:
        print(f"  Wiki quest scrape failed ({e})")

    if not quests and not wiki_quests:
        return [], None
    if not quests:
        print("  Using wiki Quests page as quest catalog fallback")
        return wiki_quests, None

    if wiki_quests:
        before = sum(len(q.get("required_items") or []) for q in quests)
        quests = merge_quest_lists(quests, wiki_quests)
        after = sum(len(q.get("required_items") or []) for q in quests)
        if after > before:
            print(f"  Enriched quest requirements from wiki (+{after - before})")
    return quests, tree


def fetch_metaforge_quest_items(*, persist: bool = True) -> dict[str, list[str]]:
    """Fetch quests and return item_name_lower -> [quest info].

    When ``persist`` is True (default), also writes quests.csv and quest
    tables in arlo.db. MetaForge is primary; wiki Quests page fills gaps.
    """
    quests, tree = fetch_quest_catalog_data()
    if not quests:
        return {}
    if persist:
        quest_map = write_quest_catalog(quests, tree=tree or {})
    else:
        quest_rows, req_rows, _prereqs = build_quest_catalog_rows(quests, tree or {})
        _ = quest_rows
        quest_map = quest_map_from_requirements(req_rows)
    print(f"  Mapped quest requirements for {len(quest_map)} items")
    return quest_map


def _normalize_category(item_type: str) -> str:
    mapping = {
        "basic_material": "Basic Material", "refined_material": "Refined Material",
        "topside_material": "Topside Material", "recyclable": "Recyclable",
        "trinket": "Trinket", "nature": "Nature", "quick_use": "Quick Use",
        "key": "Key", "ammunition": "Ammunition", "ammo": "Ammunition",
        "shield": "Shield", "augment": "Augment",
        "mods": "Mods", "mod": "Mods", "modification": "Mods",
        "weapon": "Weapon", "blueprint": "Blueprint",
    }
    return mapping.get(item_type.lower().strip(), item_type.title() if item_type else "")


# =============================================================================
# Wiki scraper
# =============================================================================

def wiki_url(path: str) -> str:
    """Build a wiki page URL from ``WIKI_URL`` + path segment (e.g. ``Quests``)."""
    return f"{WIKI_URL.rstrip('/')}/{path.lstrip('/')}"


def fetch_wiki_page(path: str = WIKI_LOOT_PATH) -> str:
    url = wiki_url(path)
    print(f"Fetching {url} ...")
    resp = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    print(f"  Got {len(resp.text):,} bytes")
    return resp.text


def _slugify(value: str) -> str:
    """Match MetaForge quest ids / progress.slugify (apostrophes dropped)."""
    cleaned = (value or "").lower().replace("'", "").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")


_WIKI_ITEM_QTY_RE = re.compile(
    r"(\d+)\s*[x×]\s*(.+?)(?=(?:\d+\s*[x×])|$)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_wiki_item_quantities(text: str) -> list[tuple[str, int]]:
    """Parse ``3× ARC Alloy`` / ``1× Battery 6× Wires`` into (name, qty) pairs."""
    raw = (text or "").replace("\u00d7", "x").strip()
    if not raw or raw in {"—", "-", "–", "N/A", "n/a"}:
        return []
    found: list[tuple[str, int]] = []
    for qty_s, name in _WIKI_ITEM_QTY_RE.findall(raw):
        name = re.sub(r"\s+", " ", name).strip(" ,;")
        if name:
            found.append((name, int(qty_s)))
    return found


def scrape_wiki_quests() -> list[dict]:
    """Scrape the wiki Quests tables into MetaForge-shaped quest dicts.

    Used as a fallback (and requirement enricher) when MetaForge is down or
    missing turn-in items. Prerequisites are not on the wiki page.
    """
    print("Scraping wiki Quests page...")
    html = fetch_wiki_page(WIKI_QUESTS_PATH)
    soup = BeautifulSoup(html, "html.parser")

    by_id: dict[str, dict] = {}
    for table in soup.find_all("table"):
        header_cells = table.find("tr")
        if not header_cells:
            continue
        headers = [
            th.get_text(strip=True).lower()
            for th in header_cells.find_all("th")
        ]
        if "quest" not in headers or "required items" not in headers:
            continue
        quest_col = headers.index("quest")
        req_col = headers.index("required items")
        trader_col = headers.index("trader") if "trader" in headers else None

        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) <= max(quest_col, req_col):
                continue
            q_cell = cells[quest_col]
            link = q_cell.find("a")
            name = (
                link.get_text(strip=True)
                if link
                else q_cell.get_text(" ", strip=True)
            )
            name = re.sub(r"\s+", " ", name or "").strip()
            if not name:
                continue
            qid = _slugify(name)
            trader = ""
            if trader_col is not None and len(cells) > trader_col:
                trader = cells[trader_col].get_text(" ", strip=True)

            required_items: list[dict] = []
            for item_name, qty in _parse_wiki_item_quantities(
                cells[req_col].get_text(" ", strip=True)
            ):
                item_id = _slugify(item_name)
                required_items.append({
                    "item_id": item_id,
                    "quantity": qty,
                    "item": {"id": item_id, "name": item_name},
                })

            existing = by_id.get(qid)
            if existing is None:
                by_id[qid] = {
                    "id": qid,
                    "name": name,
                    "trader_name": trader,
                    "xp": 0,
                    "manifest": {"prerequisites": []},
                    "required_items": required_items,
                }
            elif required_items and not existing.get("required_items"):
                existing["required_items"] = required_items

    quests = list(by_id.values())
    print(f"  Scraped {len(quests)} quests from wiki")
    return quests


def merge_quest_lists(primary: list[dict], secondary: list[dict]) -> list[dict]:
    """Prefer ``primary`` (MetaForge) ids/prereqs; union required_items from both."""
    if not primary:
        return list(secondary)
    if not secondary:
        return list(primary)

    merged: dict[str, dict] = {}
    for quest in primary:
        qid = str(quest.get("id") or "").strip()
        if qid:
            merged[qid] = quest

    for quest in secondary:
        qid = str(quest.get("id") or _slugify(str(quest.get("name") or ""))).strip()
        if not qid:
            continue
        if qid not in merged:
            merged[qid] = quest
            continue
        base = merged[qid]
        have = {
            (
                str((r.get("item") or {}).get("name") or r.get("item_id") or "")
                .strip()
                .lower()
            )
            for r in (base.get("required_items") or [])
            if isinstance(r, dict)
        }
        extra = list(base.get("required_items") or [])
        for req in quest.get("required_items") or []:
            if not isinstance(req, dict):
                continue
            iname = str(
                (req.get("item") or {}).get("name") or req.get("item_id") or ""
            ).strip().lower()
            if iname and iname not in have:
                extra.append(req)
                have.add(iname)
        base["required_items"] = extra

    return list(merged.values())


# =============================================================================
# Wiki Projects + Traders directories
# =============================================================================

def discover_wiki_directory_links(
    path: str,
    name_header: str,
) -> list[tuple[str, str]]:
    """Follow name-column links on a wiki directory page.

    Returns ``[(display_name, wiki_path), ...]`` where ``wiki_path`` is the
    ``/wiki/...`` href (fragment preserved). Skips self-links and meta pages.
    """
    print(f"Discovering links on wiki {path} page...")
    html = fetch_wiki_page(path)
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("#mw-content-text") or soup
    header_key = name_header.strip().lower()
    skip_titles = {
        path.lower(),
        "live events",
        "raider tokens",
        "raider",
        "quests",
        "speranza",
    }

    found: list[tuple[str, str]] = []
    seen_hrefs: set[str] = set()
    for table in content.find_all("table"):
        header_row = table.find("tr")
        if not header_row:
            continue
        headers = [
            th.get_text(strip=True).lower() for th in header_row.find_all("th")
        ]
        if header_key not in headers:
            continue
        col = headers.index(header_key)
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) <= col:
                continue
            link = cells[col].find("a", href=True)
            if not link:
                continue
            href = link["href"].split("#")[0]  # page path; fragment used below
            fragment = ""
            full = link["href"]
            if "#" in full:
                fragment = "#" + full.split("#", 1)[1]
            if not href.startswith("/wiki/"):
                continue
            title = href.replace("/wiki/", "").replace("_", " ")
            name = re.sub(r"\s+", " ", link.get_text(strip=True)).strip()
            if not name or name.lower() in skip_titles:
                continue
            key = href + fragment
            if key in seen_hrefs:
                continue
            seen_hrefs.add(key)
            found.append((name, href + fragment))

    print(f"  Found {len(found)} entries")
    return found


def _scope_content(soup: BeautifulSoup, project_name: str, href: str):
    """Narrow to a heading section when the directory link targets a fragment
    or a differently-titled page (e.g. Sheltered Retreat → Outpost)."""
    content = soup.select_one("#mw-content-text") or soup
    fragment = ""
    if "#" in href:
        fragment = href.split("#", 1)[1].replace("_", " ").rstrip(":")

    heading = None
    if fragment:
        for h in content.find_all(["h2", "h3", "h4"]):
            ht = re.sub(r"\s+", " ", h.get_text(" ", strip=True)).strip().rstrip(":")
            if ht.lower() == fragment.lower() or fragment.lower() in ht.lower():
                heading = h
                break
    if heading is None:
        page_title = (soup.title.get_text(strip=True) if soup.title else "")
        page_title = page_title.split("-")[0].strip()
        if _slugify(page_title) != _slugify(project_name):
            for h in content.find_all(["h2", "h3"]):
                if _slugify(h.get_text(" ", strip=True)) == _slugify(project_name):
                    heading = h
                    break
    if heading is None:
        return content

    # Collect siblings until the next same-or-higher heading level
    level = int(heading.name[1])
    parts: list[str] = [str(heading)]
    for sib in heading.next_siblings:
        if getattr(sib, "name", None) in {"h2", "h3", "h4"}:
            if int(sib.name[1]) <= level:
                break
        if getattr(sib, "name", None):
            parts.append(str(sib))
    return BeautifulSoup("".join(parts), "html.parser")


def _requirements_from_tables(root) -> list[tuple[str, int]]:
    """Pull (item_name, qty) from Requirement-column tables and qty tables."""
    found: list[tuple[str, int]] = []
    for table in root.find_all("table"):
        header_row = table.find("tr")
        if not header_row:
            continue
        headers = [
            th.get_text(strip=True).lower() for th in header_row.find_all("th")
        ]
        if "requirement" in headers:
            col = headers.index("requirement")
            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) <= col:
                    continue
                found.extend(
                    _parse_wiki_item_quantities(
                        cells[col].get_text(" ", strip=True)
                    )
                )
            continue
        # Headerless / odd tables (Converging Paths): whole-cell qty lists
        if not headers:
            for row in table.find_all("tr"):
                for cell in row.find_all(["td", "th"]):
                    found.extend(
                        _parse_wiki_item_quantities(
                            cell.get_text(" ", strip=True)
                        )
                    )
    return found


def _requirements_from_tabbers(root) -> list[tuple[str, int]]:
    """First ``ul`` in a tabber panel when a Rewards heading follows (turn-ins)."""
    found: list[tuple[str, int]] = []
    for panel in root.select(".tabber__panel"):
        first_ul = None
        has_rewards_after = False
        for child in panel.children:
            if not getattr(child, "name", None):
                continue
            if child.name == "ul" and first_ul is None:
                first_ul = child
                continue
            if first_ul is not None:
                text = child.get_text(" ", strip=True).lower()
                if child.name in {"h2", "h3", "h4"} or "reward" in text[:20]:
                    if "reward" in text:
                        has_rewards_after = True
                        break
                # mw-heading wrapper
                if "mw-heading" in (child.get("class") or []) and "reward" in text:
                    has_rewards_after = True
                    break
        if first_ul is not None and has_rewards_after:
            for li in first_ul.find_all("li", recursive=False):
                found.extend(
                    _parse_wiki_item_quantities(li.get_text(" ", strip=True))
                )
    return found


def _aggregate_requirements(
    pairs: list[tuple[str, int]],
) -> list[tuple[str, int]]:
    totals: dict[str, int] = {}
    order: list[str] = []
    for name, qty in pairs:
        key = name.strip()
        if not key:
            continue
        if key not in totals:
            order.append(key)
            totals[key] = 0
        totals[key] += qty
    return [(n, totals[n]) for n in order]


def scrape_wiki_project_page(name: str, href: str) -> dict:
    """Scrape one project page into ``{id, name, required_items:[...]}``."""
    path = href.split("#", 1)[0].replace("/wiki/", "")
    url = wiki_url(path)
    print(f"  Project: {name}")
    resp = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    root = _scope_content(soup, name, href)

    # Tables (including inside tabbers) + turn-in uls that precede Rewards
    pairs = _requirements_from_tables(root) + _requirements_from_tabbers(root)
    aggregated = _aggregate_requirements(pairs)
    required_items = [
        {
            "item_id": _slugify(item_name),
            "quantity": qty,
            "item": {"id": _slugify(item_name), "name": item_name},
        }
        for item_name, qty in aggregated
    ]
    return {
        "id": _slugify(name),
        "name": name,
        "required_items": required_items,
    }


def scrape_wiki_projects() -> list[dict]:
    """Discover Projects directory entries and scrape each for item requirements."""
    links = discover_wiki_directory_links(WIKI_PROJECTS_PATH, "Event")
    projects: list[dict] = []
    for name, href in links:
        try:
            projects.append(scrape_wiki_project_page(name, href))
            time.sleep(0.15)
        except Exception as e:
            print(f"  Skipping {name}: {e}")
    with_reqs = sum(1 for p in projects if p.get("required_items"))
    print(f"  Scraped {len(projects)} projects ({with_reqs} with item requirements)")
    return projects


def scrape_wiki_trader_page(name: str, href: str) -> dict:
    """Scrape one trader page's ``.ic-card`` shop / barter grid."""
    path = href.split("#", 1)[0].replace("/wiki/", "")
    url = wiki_url(path)
    print(f"  Trader: {name}")
    resp = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    content = soup.select_one("#mw-content-text") or soup

    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for card in content.select(".ic-card"):
        name_el = card.select_one(".ic-fname-text") or card.select_one(".ic-fname")
        link = card.select_one("a[href^='/wiki/']")
        item_name = ""
        if name_el:
            item_name = name_el.get_text(strip=True)
        elif link:
            item_name = (link.get("title") or link.get_text(strip=True) or "").strip()
        item_name = re.sub(r"\s+", " ", item_name).strip()
        if not item_name:
            continue
        item_id = _slugify(item_name)
        if item_id in seen:
            # Prefer first price (Lance lists free-loadout then paid duplicates)
            continue
        seen.add(item_id)
        price_el = card.select_one(".ic-value")
        price = price_el.get_text(strip=True) if price_el else ""
        items.append({
            "item_id": item_id,
            "item_name": item_name,
            "price": price,
        })
    return {"id": _slugify(name), "name": name, "items": items}


def scrape_wiki_traders() -> list[dict]:
    """Discover Traders directory entries and scrape each inventory."""
    links = discover_wiki_directory_links(WIKI_TRADERS_PATH, "Name")
    traders: list[dict] = []
    for name, href in links:
        try:
            traders.append(scrape_wiki_trader_page(name, href))
            time.sleep(0.15)
        except Exception as e:
            print(f"  Skipping {name}: {e}")
    total_items = sum(len(t.get("items") or []) for t in traders)
    print(f"  Scraped {len(traders)} traders ({total_items} shop items)")
    return traders


def write_projects_catalog(
    projects: list[dict],
    *,
    csv_path: Path = PROJECTS_CSV,
    db_path: Path = PROJECTS_DB,
) -> dict[str, list[str]]:
    """Persist projects.csv + project tables in arlo.db; return keep_for map."""
    project_rows: list[dict[str, str]] = []
    req_rows: list[dict[str, str]] = []
    for project in projects:
        pid = str(project.get("id") or "").strip()
        pname = str(project.get("name") or "").strip()
        if not pid or not pname:
            continue
        reqs = project.get("required_items") or []
        parts = [
            f"{r.get('item_id')}:{r.get('quantity')}:{(r.get('item') or {}).get('name') or r.get('item_id')}"
            for r in reqs
            if isinstance(r, dict) and (r.get("item_id") or (r.get("item") or {}).get("name"))
        ]
        project_rows.append({
            "id": pid,
            "name": pname,
            "required_items": "|".join(parts),
        })
        for req in reqs:
            if not isinstance(req, dict):
                continue
            item = req.get("item") if isinstance(req.get("item"), dict) else {}
            item_id = str(req.get("item_id") or item.get("id") or "").strip()
            item_name = str(item.get("name") or "").strip() or item_id
            try:
                qty = int(req.get("quantity") or 1)
            except (TypeError, ValueError):
                qty = 1
            if not item_id:
                continue
            req_rows.append({
                "project_id": pid,
                "project_name": pname,
                "item_id": item_id,
                "item_name": item_name,
                "quantity": str(qty),
            })

    project_rows.sort(key=lambda r: r["id"])
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["id", "name", "required_items"]
        )
        writer.writeheader()
        writer.writerows(project_rows)

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            DROP TABLE IF EXISTS project_requirements;
            DROP TABLE IF EXISTS projects;
            CREATE TABLE projects (
                id TEXT PRIMARY KEY NOT NULL,
                name TEXT NOT NULL
            );
            CREATE TABLE project_requirements (
                project_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                item_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                PRIMARY KEY (project_id, item_id),
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
            CREATE INDEX idx_project_req_item_name
                ON project_requirements(item_name COLLATE NOCASE);
            """
        )
        conn.executemany(
            "INSERT INTO projects (id, name) VALUES (?, ?)",
            [(r["id"], r["name"]) for r in project_rows],
        )
        conn.executemany(
            "INSERT INTO project_requirements "
            "(project_id, item_id, item_name, quantity) VALUES (?, ?, ?, ?)",
            [
                (r["project_id"], r["item_id"], r["item_name"], int(r["quantity"]))
                for r in req_rows
            ],
        )
        conn.commit()
    finally:
        conn.close()

    print(f"  Wrote {len(project_rows)} projects to {csv_path.name} / {db_path.name}")
    project_map: dict[str, list[str]] = {}
    for row in req_rows:
        project_map.setdefault(row["item_name"].lower(), []).append(
            f"{row['project_name']} ({row['quantity']}x)"
        )
    return project_map


def write_traders_catalog(
    traders: list[dict],
    *,
    csv_path: Path = TRADERS_CSV,
    db_path: Path = TRADERS_DB,
) -> None:
    """Persist traders.csv + trader tables in arlo.db."""
    trader_rows: list[dict[str, str]] = []
    item_rows: list[dict[str, str]] = []
    for trader in traders:
        tid = str(trader.get("id") or "").strip()
        tname = str(trader.get("name") or "").strip()
        if not tid or not tname:
            continue
        items = trader.get("items") or []
        trader_rows.append({
            "id": tid,
            "name": tname,
            "item_count": str(len(items)),
        })
        for item in items:
            if not isinstance(item, dict):
                continue
            item_rows.append({
                "trader_id": tid,
                "trader_name": tname,
                "item_id": str(item.get("item_id") or "").strip(),
                "item_name": str(item.get("item_name") or "").strip(),
                "price": str(item.get("price") or "").strip(),
            })

    trader_rows.sort(key=lambda r: r["id"])
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "trader_id",
                "trader_name",
                "item_id",
                "item_name",
                "price",
            ],
        )
        writer.writeheader()
        writer.writerows(item_rows)

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            DROP TABLE IF EXISTS trader_items;
            DROP TABLE IF EXISTS traders;
            CREATE TABLE traders (
                id TEXT PRIMARY KEY NOT NULL,
                name TEXT NOT NULL
            );
            CREATE TABLE trader_items (
                trader_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                item_name TEXT NOT NULL,
                price TEXT,
                PRIMARY KEY (trader_id, item_id),
                FOREIGN KEY (trader_id) REFERENCES traders(id)
            );
            CREATE INDEX idx_trader_item_name
                ON trader_items(item_name COLLATE NOCASE);
            """
        )
        conn.executemany(
            "INSERT INTO traders (id, name) VALUES (?, ?)",
            [(r["id"], r["name"]) for r in trader_rows],
        )
        conn.executemany(
            "INSERT INTO trader_items "
            "(trader_id, item_id, item_name, price) VALUES (?, ?, ?, ?)",
            [
                (r["trader_id"], r["item_id"], r["item_name"], r["price"] or None)
                for r in item_rows
                if r["item_id"]
            ],
        )
        conn.commit()
    finally:
        conn.close()

    print(
        f"  Wrote {len(trader_rows)} traders "
        f"({len(item_rows)} items) to {csv_path.name} / {db_path.name}"
    )


def persist_wiki_side_catalogs() -> dict[str, list[str]]:
    """Scrape + write projects/traders catalogs. Returns project item map."""
    project_map: dict[str, list[str]] = {}
    try:
        projects = scrape_wiki_projects()
        if projects:
            project_map = write_projects_catalog(projects)
    except Exception as e:
        print(f"  Projects catalog skipped ({e})")
    try:
        traders = scrape_wiki_traders()
        if traders:
            write_traders_catalog(traders)
    except Exception as e:
        print(f"  Traders catalog skipped ({e})")
    return project_map


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


def scrape_wiki_uses() -> dict[str, str]:
    """
    Scrape ONLY the Uses column from the wiki loot table.
    Returns a dict of item_name_lower -> uses_string.
    """
    print("Scraping wiki for workshop/project/expedition data...")
    html = fetch_wiki_page()
    soup = BeautifulSoup(html, "html.parser")

    loot_table = None
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if "item" in headers and "rarity" in headers and "recycles to" in headers:
            loot_table = table
            break

    if not loot_table:
        print("  Could not find wiki loot table")
        return {}

    header_row = loot_table.find("tr")
    headers = [th.get_text(strip=True).lower() for th in header_row.find_all("th")]

    item_col = None
    uses_col = None
    for i, h in enumerate(headers):
        if h == "item":
            item_col = i
        elif h == "uses":
            uses_col = i

    if item_col is None or uses_col is None:
        print("  Could not find Item or Uses columns")
        return {}

    uses_map: dict[str, str] = {}
    for row in loot_table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) <= max(item_col, uses_col):
            continue

        item_cell = cells[item_col]
        name_link = item_cell.find("a")
        name = name_link.get_text(strip=True) if name_link else item_cell.get_text(strip=True)
        if not name:
            continue

        uses = _parse_uses(cells[uses_col])
        if uses:
            uses_map[name.lower()] = uses

    print(f"  Found wiki uses for {len(uses_map)} items")
    return uses_map


def fetch_items_from_wiki() -> list[GameItem]:
    """Full wiki scrape - used as complete fallback."""
    html = fetch_wiki_page()
    soup = BeautifulSoup(html, "html.parser")

    loot_table = None
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if "item" in headers and "rarity" in headers and "recycles to" in headers:
            loot_table = table
            break

    if not loot_table:
        print("ERROR: Could not find the loot table!")
        return []

    header_row = loot_table.find("tr")
    headers = [th.get_text(strip=True).lower() for th in header_row.find_all("th")]

    col_map = {}
    for i, h in enumerate(headers):
        if h == "item": col_map["item"] = i
        elif h == "rarity": col_map["rarity"] = i
        elif "recycles" in h: col_map["recycles_to"] = i
        elif "sell" in h: col_map["sell_price"] = i
        elif "stack" in h: col_map["stack_size"] = i
        elif h == "category": col_map["category"] = i
        elif h == "uses": col_map["uses"] = i

    items: list[GameItem] = []
    for row in loot_table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        def get_cell(key: str):
            idx = col_map.get(key)
            return cells[idx] if idx is not None and idx < len(cells) else None

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
# Hybrid fetch: MetaForge + Wiki enrichment
# =============================================================================

def fetch_items_hybrid(*, persist_side_catalogs: bool = True) -> list[GameItem]:
    """
    Best of both worlds:
      - MetaForge API for items, prices, stack sizes, recycle data, quest data
      - Wiki for workshop uses, projects, traders, and quest fallback
    """
    # 1. MetaForge: items + recycle + quests
    raw_items = fetch_metaforge_items()
    recycle_map = fetch_metaforge_recycle_map(raw_items)
    quest_map = fetch_metaforge_quest_items(persist=persist_side_catalogs)

    # 2. Wiki: Uses column + Projects/Traders directories
    try:
        wiki_uses = scrape_wiki_uses()
    except Exception as e:
        print(f"  Wiki scrape failed ({e}), continuing without wiki uses")
        wiki_uses = {}

    project_map: dict[str, list[str]] = {}
    if persist_side_catalogs:
        project_map = persist_wiki_side_catalogs()

    # 3. Combine into GameItems
    items: list[GameItem] = []
    for raw in raw_items:
        name = raw.get("name", "")
        if not name:
            continue

        stat_block = raw.get("stat_block") or {}
        category = _normalize_category(raw.get("item_type", ""))
        sell_price = raw.get("value", 0) or 0
        stack_size = stat_block.get("stackSize", 0) or 0
        rarity = raw.get("rarity", "")
        recycles_to = recycle_map.get(name, "")

        combined_uses = format_keep_for(
            wiki_uses.get(name.lower(), ""),
            quest_map.get(name.lower(), []),
            project_map.get(name.lower(), []),
        )

        items.append(GameItem(
            name=name,
            rarity=rarity,
            recycles_to=recycles_to,
            sell_price=sell_price,
            stack_size=stack_size,
            category=category,
            uses=combined_uses,
        ))

    print(f"\n  Processed {len(items)} items (MetaForge + Wiki enrichment)")
    return items


# =============================================================================
# Keep-for formatting / merge
# =============================================================================

def normalize_keep_labels(keep_for: str) -> str:
    """Decode HTML entities and ensure Projects/Quests/Workshop trailing colons."""
    text = (keep_for or "").strip()
    if not text:
        return ""
    text = html.unescape(text)
    text = text.replace("[[", "").replace("]]", "")
    text = re.sub(r"\bQuests(?!:)\s+", "Quests: ", text)
    text = re.sub(r"\bProjects(?!:)\s+", "Projects: ", text)
    text = re.sub(r"\bWorkshop(?!:)\s+", "Workshop: ", text)
    return text


def format_keep_for(
    wiki_uses: str = "",
    quest_uses: list[str] | None = None,
    project_uses: list[str] | None = None,
) -> str:
    """Build a consistent keep_for string from wiki uses + quests + projects.

    MetaForge/wiki quests are ``Quests: Name (Nx), ...``. Structured project
    requirements become ``Projects: Name (Nx), ...`` when not already covered
    by the wiki Uses cell. Workshop text stays in the wiki portion.
    """
    parts: list[str] = []
    wiki = normalize_keep_labels(wiki_uses or "")
    if wiki:
        parts.append(wiki)

    wiki_lower = wiki.lower()

    quests = quest_uses or []
    if quests:
        filtered: list[str] = []
        for entry in quests:
            quest_name = re.sub(r"\s*\(\d+x\)\s*$", "", entry).strip()
            if quest_name and quest_name.lower() not in wiki_lower:
                filtered.append(entry)
        if filtered:
            parts.append("Quests: " + ", ".join(filtered))

    projects = project_uses or []
    if projects:
        filtered_p: list[str] = []
        for entry in projects:
            project_name = re.sub(r"\s*\(\d+x\)\s*$", "", entry).strip()
            if project_name and project_name.lower() not in wiki_lower:
                filtered_p.append(entry)
        if filtered_p:
            parts.append("Projects: " + ", ".join(filtered_p))

    return "; ".join(parts)


def merge_keep_for(old: str, new: str) -> str:
    """Prefer freshly scraped keep_for when present; otherwise keep the old note."""
    new = normalize_keep_labels(new or "")
    old = normalize_keep_labels(old or "")
    return new if new else old


_BARE_DISPOSE_ACTIONS = frozenset({
    "recycle",
    "sell",
    "trash",
    "recycle if short on materials; sell otherwise",
})

# keep_for values that mean a real progression hold (not "Quick Use consumable")
_PROGRESS_KEEP_RE = re.compile(
    r"quests?|projects?|workshop|upgrade|expedition|scrappy|"
    r"flickering\s+flames|\bstage\b",
    re.IGNORECASE,
)


def is_bare_dispose_action(action: str) -> bool:
    """True for plain Recycle/Sell actions with no keep-until wording."""
    return action.strip().lower() in _BARE_DISPOSE_ACTIONS


def has_progress_keep(keep_for: str) -> bool:
    """True when keep_for names a quest, project, workshop, or upgrade use."""
    return bool(_PROGRESS_KEEP_RE.search(keep_for or ""))


def merge_action(old_action: str, new_action: str, keep_for: str) -> str:
    """Preserve hand-tuned actions, but fix Recycle/Sell that contradict keep advice."""
    old_action = (old_action or "").strip()
    new_action = (new_action or "").strip()
    new_lower = new_action.lower()
    # Bare Recycle/Sell + quest/project/workshop keep_for → use generated keep-until
    if (
        has_progress_keep(keep_for)
        and is_bare_dispose_action(old_action)
        and new_action
        and not is_bare_dispose_action(new_action)
    ):
        return new_action
    # Bare Sell/Recycle → Keep when category rules now say Keep (weapons, blueprints, …)
    if (
        is_bare_dispose_action(old_action)
        and new_lower.startswith("keep")
    ):
        return new_action
    return old_action or new_action


# =============================================================================
# Action generation
# =============================================================================

def generate_action(item: GameItem) -> tuple[str, str, str]:
    has_uses = bool(item.uses.strip())
    can_recycle = bool(item.recycles_to.strip())
    category = item.category.strip()

    keep_for = item.uses if has_uses else ""
    recycle_for = item.recycles_to

    if category == "Key":
        return "Keep", "", "Unlocks locked areas"
    if category == "Quick Use":
        return ("Keep", recycle_for, keep_for) if has_uses else ("Use", recycle_for, "Quick Use consumable")
    if category == "Ammunition":
        return "Keep", "", "Ammo"
    if category in {"Shield", "Augment", "Mods", "Weapon"}:
        return "Keep", recycle_for, f"Equippable {category}"
    if category == "Blueprint":
        return (
            "Keep",
            recycle_for,
            "Learn, sell, or trade to other players",
        )
    if category in MATERIAL_CATEGORIES:
        return ("Keep", recycle_for, keep_for) if has_uses else ("Keep", recycle_for, f"{category} - crafting ingredient")
    if category == "Nature":
        return ("Keep", recycle_for, keep_for) if has_uses else ("Sell", recycle_for, "")
    if category == "Trinket":
        return ("Keep until uses complete; sell after", "", keep_for) if has_uses else ("Sell", "", "")
    if category == "Recyclable":
        if has_uses:
            return "Keep until uses complete; recycle after", recycle_for, keep_for
        if can_recycle:
            return ("Recycle if short on materials; Sell otherwise", recycle_for, "") if item.sell_price >= 2000 else ("Recycle", recycle_for, "")
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
        for row in csv.DictReader(f):
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
        key = item.name.lower()
        sell_price = str(item.sell_price) if item.sell_price else ""
        stack_size = str(item.stack_size) if item.stack_size else ""

        if merge and existing and key in existing:
            old = existing[key]
            merged_keep = merge_keep_for(old["keep_for"], keep_for)
            rows.append({
                "name": item.name,
                # Preserve hand-tuned actions, except bare Recycle/Sell that
                # contradict a real quest/project/workshop keep_for.
                "action": merge_action(old["action"], action, merged_keep),
                "recycle_for": old["recycle_for"] or recycle_for,
                "keep_for": merged_keep,
                "sell_price": sell_price,
                "stack_size": stack_size,
            })
            stats.items_preserved += 1
        else:
            rows.append({
                "name": item.name, "action": action,
                "recycle_for": recycle_for,
                "keep_for": normalize_keep_labels(keep_for),
                "sell_price": sell_price, "stack_size": stack_size,
            })
            if existing and key not in (existing or {}):
                stats.items_new += 1

    rows.sort(key=lambda r: r["name"].lower())
    stats.items_written = len(rows)
    return rows, stats


def write_csv(rows: list[dict[str, str]], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "action", "recycle_for", "keep_for", "sell_price", "stack_size"])
        writer.writeheader()
        writer.writerows(rows)


def rebuild_database(csv_path: Path, db_path: Path) -> int:
    """Rebuild the ``items`` table in the shared catalog DB from items.csv."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
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
            for row in csv.DictReader(f):
                name = row.get("name", "").strip()
                action = row.get("action", "").strip()
                if not name or not action:
                    continue
                recycle_for = row.get("recycle_for", "").strip() or None
                keep_for = row.get("keep_for", "").strip() or None
                sp = row.get("sell_price", "").strip()
                ss = row.get("stack_size", "").strip()

                conn.execute(
                    "INSERT INTO items (name, action, recycle_for, keep_for, sell_price, stack_size) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                    "action=excluded.action, recycle_for=excluded.recycle_for, "
                    "keep_for=excluded.keep_for, sell_price=excluded.sell_price, stack_size=excluded.stack_size",
                    (name, action, recycle_for, keep_for, int(sp) if sp else None, int(ss) if ss else None),
                )
                count += 1

        conn.commit()
        return count
    finally:
        conn.close()


def _parse_encoded_requirements(encoded: str) -> list[tuple[str, str, int]]:
    """Parse ``item_id:qty:Name|...`` into ``(item_id, item_name, qty)``."""
    out: list[tuple[str, str, int]] = []
    for part in (encoded or "").split("|"):
        part = part.strip()
        if not part:
            continue
        bits = part.split(":", 2)
        if len(bits) < 2:
            continue
        item_id = bits[0].strip()
        try:
            qty = int(bits[1].strip())
        except ValueError:
            continue
        item_name = bits[2].strip() if len(bits) > 2 else item_id
        if item_id:
            out.append((item_id, item_name or item_id, qty))
    return out


def rebuild_quests_from_csv(
    csv_path: Path = QUESTS_CSV,
    db_path: Path = ARLO_DB,
) -> int:
    """Load quests.csv into the shared catalog DB."""
    if not csv_path.is_file():
        return 0
    quest_rows: list[dict[str, str]] = []
    req_rows: list[dict[str, str]] = []
    prereq_rows: list[dict[str, str]] = []
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            qid = (row.get("id") or "").strip()
            name = (row.get("name") or "").strip()
            if not qid or not name:
                continue
            quest_rows.append({
                "id": qid,
                "name": name,
                "trader_name": (row.get("trader_name") or "").strip(),
                "xp": (row.get("xp") or "0").strip() or "0",
                "prerequisites": (row.get("prerequisites") or "").strip(),
            })
            for parent in (row.get("prerequisites") or "").split(";"):
                parent = parent.strip()
                if parent:
                    prereq_rows.append({
                        "quest_id": qid,
                        "parent_quest_id": parent,
                        "required_state": "COMPLETED",
                    })
            for item_id, item_name, qty in _parse_encoded_requirements(
                row.get("required_items") or ""
            ):
                req_rows.append({
                    "quest_id": qid,
                    "quest_name": name,
                    "item_id": item_id,
                    "item_name": item_name,
                    "quantity": str(qty),
                })
    return rebuild_quests_database(quest_rows, req_rows, prereq_rows, db_path)


def rebuild_projects_from_csv(
    csv_path: Path = PROJECTS_CSV,
    db_path: Path = ARLO_DB,
) -> int:
    """Load projects.csv into the shared catalog DB."""
    if not csv_path.is_file():
        return 0
    projects: list[dict] = []
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pid = (row.get("id") or "").strip()
            name = (row.get("name") or "").strip()
            if not pid or not name:
                continue
            required_items = [
                {
                    "item_id": item_id,
                    "quantity": qty,
                    "item": {"id": item_id, "name": item_name},
                }
                for item_id, item_name, qty in _parse_encoded_requirements(
                    row.get("required_items") or ""
                )
            ]
            projects.append({
                "id": pid,
                "name": name,
                "required_items": required_items,
            })
    # Write DB tables only (CSV already exists)
    write_projects_catalog(projects, csv_path=csv_path, db_path=db_path)
    return len(projects)


def rebuild_traders_from_csv(
    csv_path: Path = TRADERS_CSV,
    db_path: Path = ARLO_DB,
) -> int:
    """Load traders.csv into the shared catalog DB."""
    if not csv_path.is_file():
        return 0
    by_trader: dict[str, dict] = {}
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = (row.get("trader_id") or "").strip()
            tname = (row.get("trader_name") or "").strip()
            if not tid:
                continue
            bucket = by_trader.setdefault(
                tid, {"id": tid, "name": tname or tid, "items": []}
            )
            item_id = (row.get("item_id") or "").strip()
            if not item_id:
                continue
            bucket["items"].append({
                "item_id": item_id,
                "item_name": (row.get("item_name") or item_id).strip(),
                "price": (row.get("price") or "").strip(),
            })
    traders = list(by_trader.values())
    write_traders_catalog(traders, csv_path=csv_path, db_path=db_path)
    return len(traders)


def rebuild_arlo_db_from_csvs(*, db_path: Path = ARLO_DB) -> None:
    """Rebuild the full shared catalog DB from on-disk CSVs (no network)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    items_n = rebuild_database(ITEMS_CSV, db_path) if ITEMS_CSV.is_file() else 0
    quests_n = rebuild_quests_from_csv(QUESTS_CSV, db_path)
    projects_n = rebuild_projects_from_csv(PROJECTS_CSV, db_path)
    traders_n = rebuild_traders_from_csv(TRADERS_CSV, db_path)
    print(
        f"  Rebuilt {db_path.name}: "
        f"{items_n} items, {quests_n} quests, "
        f"{projects_n} projects, {traders_n} traders"
    )


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Update catalog CSVs and arlo.db "
            "from MetaForge API + Arc Raiders Wiki."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python update_db.py               # Hybrid update (MetaForge + Wiki)
  python update_db.py --merge       # Keep existing manual overrides
  python update_db.py --dry-run     # Preview changes without writing
  python update_db.py --source wiki # Wiki-only mode
  python update_db.py --source api  # MetaForge-only mode (no wiki)
        """,
    )
    parser.add_argument("--merge", action="store_true", help="Preserve existing action overrides")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    parser.add_argument("--csv-only", action="store_true", help="Update CSV only, skip DB rebuild")
    parser.add_argument("--source", choices=["hybrid", "api", "wiki"], default="hybrid",
                        help="Data source: 'hybrid' (default), 'api' MetaForge only, 'wiki' only")

    args = parser.parse_args()

    persist_side = not args.dry_run

    if args.source == "wiki":
        items = fetch_items_from_wiki()
        source_name = "Arc Raiders Wiki"
        if persist_side:
            persist_wiki_side_catalogs()
            fetch_metaforge_quest_items(persist=True)  # still try API; wiki enrich inside
    elif args.source == "api":
        items = fetch_items_from_metaforge_only(persist_side_catalogs=persist_side)
        source_name = "MetaForge API"
    else:
        try:
            items = fetch_items_hybrid(persist_side_catalogs=persist_side)
            source_name = "MetaForge API + Wiki"
        except Exception as e:
            print(f"\nHybrid fetch failed ({e}), falling back to wiki only...")
            items = fetch_items_from_wiki()
            source_name = "Arc Raiders Wiki (fallback)"
            if persist_side:
                persist_wiki_side_catalogs()

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
        print("\n[DRY RUN] Preview of first 20 items:\n")
        print(f"  {'Name':<35} {'Action':<40} {'Sell':>7} {'Stack':>5}  {'Recycle For':<25}")
        print(f"  {'-'*35} {'-'*40} {'-'*7} {'-'*5}  {'-'*25}")
        for row in rows[:20]:
            print(f"  {row['name']:<35} {row['action']:<40} {row.get('sell_price',''):>7} {row.get('stack_size',''):>5}  {row['recycle_for']:<25}")
        print(f"\n  ... and {len(rows) - 20} more items")
        return

    write_csv(rows, ITEMS_CSV)
    print(f"\n  Wrote {len(rows)} items to {ITEMS_CSV}")

    if not args.csv_only:
        # Side catalogs already wrote quest/project/trader tables into ARLO_DB;
        # refresh the items table (and re-sync others from CSV if present).
        rebuild_arlo_db_from_csvs(db_path=ARLO_DB)

    print("\nDone!")


def fetch_items_from_metaforge_only(*, persist_side_catalogs: bool = True) -> list[GameItem]:
    """MetaForge-only mode (quests yes; optional wiki projects/traders catalogs)."""
    raw_items = fetch_metaforge_items()
    recycle_map = fetch_metaforge_recycle_map(raw_items)
    quest_map = fetch_metaforge_quest_items(persist=persist_side_catalogs)
    project_map: dict[str, list[str]] = {}
    if persist_side_catalogs:
        project_map = persist_wiki_side_catalogs()

    items: list[GameItem] = []
    for raw in raw_items:
        name = raw.get("name", "")
        if not name:
            continue
        stat_block = raw.get("stat_block") or {}
        items.append(GameItem(
            name=name, rarity=raw.get("rarity", ""),
            recycles_to=recycle_map.get(name, ""),
            sell_price=raw.get("value", 0) or 0,
            stack_size=stat_block.get("stackSize", 0) or 0,
            category=_normalize_category(raw.get("item_type", "")),
            uses=format_keep_for(
                "",
                quest_map.get(name.lower(), []),
                project_map.get(name.lower(), []),
            ),
        ))
    print(f"  Processed {len(items)} items from MetaForge")
    return items


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
        return (time.time() - last_ts) / 3600 >= AUTO_UPDATE_INTERVAL_HOURS
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
    Uses hybrid mode (MetaForge + Wiki) with merge, throttled to 24h.
    Falls through gracefully on any error.
    """
    if not force and not _should_auto_update():
        return False

    try:
        print("Checking for item database updates...")
        try:
            items = fetch_items_hybrid()
        except Exception:
            print("  Hybrid fetch failed, trying wiki only...")
            try:
                items = fetch_items_from_wiki()
            except Exception:
                print("  All sources unavailable, skipping update.")
                return False

        if not items:
            return False

        existing = load_existing_csv(ITEMS_CSV)
        rows, stats = build_csv_rows(items, existing, merge=True)

        write_csv(rows, ITEMS_CSV)
        rebuild_arlo_db_from_csvs(db_path=ARLO_DB)
        _mark_updated()

        new_count = stats.items_written - stats.items_preserved
        print(
            f"  Database updated: {stats.items_written} items "
            f"({new_count} new, {stats.items_preserved} preserved) → {ARLO_DB.name}"
        )
        return True

    except Exception as e:  # noqa: BLE001
        print(f"  Database update skipped (error: {e})")
        return False


if __name__ == "__main__":
    main()
