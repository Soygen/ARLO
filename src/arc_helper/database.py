"""
Database module for ARLO.
SQLite operations for item recommendations with Pydantic models.
"""

import csv
import difflib
import re
import sqlite3
from contextlib import suppress
from pathlib import Path

from pydantic import BaseModel
from pydantic import Field

from .config import DATA_DIR
from .config import get_settings


def _match_key(name: str) -> str:
    """Lowercase, alphanumeric-only key for OCR-tolerant name matching.

    OCR frequently drops spaces ("IL TORO II" -> "ILTOROII") or appends stray
    punctuation, so collapsing to alphanumerics lets those still match the
    stored name. Roman-numeral tiers (I/V) survive, so variants stay distinct.
    """
    return "".join(c for c in name.lower() if c.isalnum())


# OCR often swaps these digits/letters (5TH→STH, Mk.1→MKL, …)
_OCR_FOLD = str.maketrans({"0": "o", "1": "i", "l": "i", "5": "s", "8": "b"})

# OCR name-matching length / similarity thresholds
_NAME_KEY_LEN = 6
_SHORT_NAME_RATIO = 0.8
_MIN_STEM_LEN = 5
_MAX_PREFIX_EXTRA = 12
_MAX_SUFFIX_EXTRA = 18
_MAX_CONTAINED_PREFIX = 8
_MAX_CONTAINED_SUFFIX = 4
_MIN_CATALOG_STEM_LEN = 8
_MAX_UI_PREFIX = 10
_MAX_UI_SUFFIX = 2
_LONG_KEY_MIN_LEN = 10
_MIN_TOKEN_PAIR = 2


def _ocr_fold_key(name: str) -> str:
    """``_match_key`` with common digit/letter confusions collapsed."""
    return _match_key(name).translate(_OCR_FOLD)


def names_match(a: str, b: str, *, cutoff: float = 0.85) -> bool:
    """True if two item-name strings are the same item under OCR noise.

    Used to keep the overlay sticky while hovering: brief OCR typos should not
    count as a different item and redraw/flicker the popup. Prefix pairs
    (e.g. "RASCAL I" vs "RASCAL III") are rejected so Roman-numeral tiers
    stay distinct even though they score high on raw similarity.
    """
    if not a or not b:
        return False
    if a.strip().lower() == b.strip().lower():
        return True
    ka, kb = _match_key(a), _match_key(b)
    if not ka or not kb:
        return False
    if ka == kb or _ocr_fold_key(a) == _ocr_fold_key(b):
        return True
    # Different tiers / trailing OCR drop often look like a prefix pair
    if ka.startswith(kb) or kb.startswith(ka):
        return False
    ratio = max(
        difflib.SequenceMatcher(None, ka, kb).ratio(),
        difflib.SequenceMatcher(None, _ocr_fold_key(a), _ocr_fold_key(b)).ratio(),
    )
    # Short names: allow a single substitution (WIRES↔WIRE5 ≈ 0.8)
    if max(len(ka), len(kb)) <= _NAME_KEY_LEN:
        return ratio >= _SHORT_NAME_RATIO
    return ratio >= cutoff


# UI chrome OCR often glues onto the item name (crafting / recycle panels).
_OCR_UI_NOISE_RE = re.compile(
    r"(?:required\s*resources|requiredresources|\bsplit\s*stack\b|"
    r"\baugment\s*effect\b|\bresources?\b)",
    re.IGNORECASE,
)


def _scrub_ocr_query(name: str) -> str:
    """Strip glued UI labels so the residual can match a catalog name."""
    text = (name or "").strip()
    if not text:
        return ""
    text = _OCR_UI_NOISE_RE.sub(" ", text)
    # Leading action verb from recycle/confirm panels (spaced or glued).
    # OCR often mangles RECYCLE to CYCLE/RECYC or leaves a lone leading E
    # glued onto DEPRECATED… titles ("EDEPRECATEDANVILS").
    text = re.sub(
        # [a-z] only: IGNORECASE already folds case (avoids duplicate class ranges).
        r"^(?:recycl(?:e)?|cycle|keep|sell)(?:\s+|(?=[a-z]))",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^e(?=deprecated)", "", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _is_prefix_hit(target: str, ck: str) -> bool:
    """True when ``target`` is a valid stem prefix of catalog key ``ck``."""
    return (
        len(target) >= _MIN_STEM_LEN
        and ck.startswith(target)
        and 0 < len(ck) - len(target) <= _MAX_PREFIX_EXTRA
    )


def _is_suffix_hit(target: str, ck: str) -> bool:
    """True when ``target`` is a valid trailing suffix of catalog key ``ck``."""
    return (
        len(target) >= _MIN_STEM_LEN
        and ck.endswith(target)
        and 0 < len(ck) - len(target) <= _MAX_SUFFIX_EXTRA
    )


def _is_contained_hit(target: str, ck: str) -> bool:
    """True when ``target`` sits inside ``ck`` with a short prefix/suffix gap,
    or when ``ck`` is a long enough stem sitting inside a UI-prefixed ``target``."""
    if len(target) < _NAME_KEY_LEN or target == ck:
        return False
    idx = ck.find(target)
    if idx >= 0 and idx <= _MAX_CONTAINED_PREFIX:
        return len(ck) - idx - len(target) <= _MAX_CONTAINED_SUFFIX
    # Catalog key contained in a UI-prefixed query (``CYCLEARPEGGIOI`` → ``Arpeggio I``).
    qidx = target.find(ck)
    return (
        len(ck) >= _MIN_CATALOG_STEM_LEN
        and len(ck) * 2 >= len(target)
        and qidx >= 0
        and qidx <= _MAX_UI_PREFIX
        and len(target) - qidx - len(ck) <= _MAX_UI_SUFFIX
    )


def _classify_ocr_row(
    row: sqlite3.Row,
    target: str,
    folded: str,
    exact: list,
    folded_hits: list,
    prefix_hits: list,
    suffix_hits: list,
    contained_hits: list,
) -> None:
    """Bucket one catalog row into the appropriate hit list."""
    ck = _match_key(row["name"])
    if not ck:
        return
    if ck == target:
        exact.append(row)
        return
    if _ocr_fold_key(row["name"]) == folded:
        folded_hits.append(row)
        return
    if _is_prefix_hit(target, ck):
        prefix_hits.append(row)
    if _is_suffix_hit(target, ck):
        suffix_hits.append(row)
    if _is_contained_hit(target, ck):
        contained_hits.append(row)


def _fuzzy_best(
    rows: list[sqlite3.Row], target: str, folded: str
) -> sqlite3.Row | None:
    """Return the highest-similarity row that isn't a bare prefix pair."""
    best: sqlite3.Row | None = None
    best_ratio = 0.0
    for row in rows:
        ck = _match_key(row["name"])
        if not ck:
            continue
        if (ck.startswith(target) or target.startswith(ck)) and ck != target:
            continue
        ratio = max(
            difflib.SequenceMatcher(None, target, ck).ratio(),
            difflib.SequenceMatcher(None, folded, _ocr_fold_key(row["name"])).ratio(),
        )
        if ratio > best_ratio:
            best_ratio = ratio
            best = row
    if best is None:
        return None
    best_len = len(_match_key(best["name"]))
    cutoff = 0.82 if min(len(target), best_len) >= _LONG_KEY_MIN_LEN else 0.9
    return best if best_ratio >= cutoff else None


def _shortest_in_group(group: list[sqlite3.Row]) -> sqlite3.Row | None:
    if not group:
        return None
    group.sort(key=lambda r: (len(_match_key(r["name"])), len(r["name"]), r["name"]))
    return group[0]


def _pick_row_by_single_ocr_key(
    scrubbed: str, rows: list[sqlite3.Row]
) -> sqlite3.Row | None:
    """Match one scrubbed OCR fragment against catalog keys."""
    target = _match_key(scrubbed)
    folded = _ocr_fold_key(scrubbed)
    if not target:
        return None

    exact: list[sqlite3.Row] = []
    folded_hits: list[sqlite3.Row] = []
    prefix_hits: list[sqlite3.Row] = []
    suffix_hits: list[sqlite3.Row] = []
    contained_hits: list[sqlite3.Row] = []

    for row in rows:
        _classify_ocr_row(
            row,
            target,
            folded,
            exact,
            folded_hits,
            prefix_hits,
            suffix_hits,
            contained_hits,
        )

    if len(exact) == 1:
        return exact[0]
    if len(folded_hits) == 1:
        return folded_hits[0]
    if len(suffix_hits) == 1:
        return suffix_hits[0]
    if len(contained_hits) == 1:
        return contained_hits[0]

    # Fuzzy on keys (not raw display strings — those fail when spaces drop)
    best = _fuzzy_best(rows, target, folded)
    if best is not None:
        return best

    # Truncated stem with several tiered variants → prefer shortest catalog name
    for group in (prefix_hits, suffix_hits, contained_hits):
        result = _shortest_in_group(group)
        if result is not None:
            return result
    return None


def _pick_row_by_ocr_key(
    clean_name: str, rows: list[sqlite3.Row]
) -> sqlite3.Row | None:
    """Resolve OCR noise against catalog names using alphanumeric keys.

    Handles dropped spaces/punct (``LOOTINGMK``), digit↔letter swaps
    (``…STH EDITION`` vs ``…5TH EDITION``), truncated tier suffixes
    (``LOOTINGMK`` → shortest ``Looting Mk. …`` when the digit was lost),
    dropped leading letters (``ERALENS`` → ``Camera Lens``), UI chrome
    glued onto the name (``…DESIGN REQUIREDRESOURCES``), and recycle-dialog
    OCR that joins the panel title with the hovered tooltip name
    (``EDEPRECATEDANVILS AMPLIFIEDFRAGMENTS``).
    """
    scrubbed = _scrub_ocr_query(clean_name)
    if not scrubbed:
        return None

    hit = _pick_row_by_single_ocr_key(scrubbed, rows)
    if hit is not None:
        return hit

    # Recycle/confirm panels often OCR as two uppercase lines joined with a
    # space (truncated title + tooltip). Try each token; prefer the last unique
    # hit so the hovered tooltip wins over dialog-title chrome.
    tokens = scrubbed.split()
    if len(tokens) < _MIN_TOKEN_PAIR:
        return None
    last_hit: sqlite3.Row | None = None
    for tok in tokens:
        if len(_match_key(tok)) < _NAME_KEY_LEN:
            continue
        tok_hit = _pick_row_by_single_ocr_key(tok, rows)
        if tok_hit is not None:
            last_hit = tok_hit
    return last_hit


class Item(BaseModel):
    """Item model matching database schema.

    ``quest_needed`` is runtime-only (progress filter); not stored in arlo.db.
    """

    name: str = Field(..., min_length=1, max_length=200)
    action: str = Field(..., min_length=1, max_length=100)
    recycle_for: str | None = Field(default=None, max_length=500)
    keep_for: str | None = Field(default=None, max_length=500)
    sell_price: int | None = Field(default=None)
    stack_size: int | None = Field(default=None)
    quest_needed: int | None = Field(
        default=None,
        description="Total qty still needed across incomplete catalog quests",
    )
    project_needed: int | None = Field(
        default=None,
        description="Total qty still needed across incomplete catalog projects",
    )
    buy_from: str | None = Field(
        default=None,
        description="Trader buy hint, e.g. 'Celeste (1)'",
    )


class Database:
    """SQLite database handler for items."""

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialize database connection."""
        if db_path is None:
            db_path = get_settings().database_path
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    name TEXT PRIMARY KEY NOT NULL COLLATE NOCASE,
                    action TEXT NOT NULL,
                    recycle_for TEXT,
                    keep_for TEXT,
                    sell_price INTEGER,
                    stack_size INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_items_name
                ON items(name COLLATE NOCASE)
            """)

            # Migrate: add new columns if upgrading from older schema
            existing_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(items)").fetchall()
            }
            if "sell_price" not in existing_cols:
                conn.execute("ALTER TABLE items ADD COLUMN sell_price INTEGER")
            if "stack_size" not in existing_cols:
                conn.execute("ALTER TABLE items ADD COLUMN stack_size INTEGER")

            conn.commit()

    def lookup(self, name: str) -> Item | None:
        """Look up an item by name (case-insensitive, with fuzzy matching)."""
        clean_name = name.strip()

        with self._get_connection() as conn:
            # First try exact match
            cursor = conn.execute(
                "SELECT * FROM items WHERE name = ? COLLATE NOCASE",
                (clean_name,),
            )
            row = cursor.fetchone()

            # OCR-tolerant key match (dropped spaces, 5↔S, truncated Mk tier, …)
            if not row:
                candidates = list(conn.execute("SELECT * FROM items"))
                row = _pick_row_by_ocr_key(clean_name, candidates)

            # Substring LIKE (helps when OCR keeps spaces but not full punctuation)
            if not row:
                cursor = conn.execute(
                    """
                    SELECT * FROM items
                    WHERE name LIKE ? COLLATE NOCASE
                    ORDER BY LENGTH(name) ASC
                    LIMIT 1
                    """,
                    (f"%{clean_name}%",),
                )
                row = cursor.fetchone()

            # Display-string fuzzy match as last resort
            if not row:
                cursor = conn.execute("SELECT name FROM items")
                all_names = [r[0] for r in cursor.fetchall()]
                matches = difflib.get_close_matches(
                    clean_name, all_names, n=1, cutoff=0.8
                )
                if matches:
                    cursor = conn.execute(
                        "SELECT * FROM items WHERE name = ?", (matches[0],)
                    )
                    row = cursor.fetchone()

            if row:
                return self._row_to_item(row)
            return None

    def count(self) -> int:
        """Get total item count."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM items")
            return cursor.fetchone()[0]

    def load_csv(self, csv_path: Path | str, *, clear_existing: bool = True) -> int:
        """
        Load items from a CSV file into the database.

        CSV must have columns: name, action, recycle_for, keep_for
        Optional columns: sell_price, stack_size

        Args:
            csv_path: Path to the CSV file
            clear_existing: If True, clear existing items before loading

        Returns:
            Number of items loaded
        """
        csv_path = Path(csv_path)
        if not csv_path.exists():
            msg = f"CSV file not found: {csv_path}"
            raise FileNotFoundError(msg)

        if clear_existing:
            self.clear()

        count = 0
        with self._get_connection() as conn:
            with Path(csv_path).open(encoding="utf-8") as f:
                reader = csv.DictReader(f)

                for row in reader:
                    name = row.get("name", "").strip()
                    action = row.get("action", "").strip()
                    recycle_for = row.get("recycle_for", "").strip() or None
                    keep_for = row.get("keep_for", "").strip() or None

                    # New columns (optional in CSV for backwards compat)
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

    @staticmethod
    def log_missing_item(name: str) -> None:
        """Log an unknown item to missing_items.csv for easy addition later.

        Writes under ``data/`` (not beside the DB) and de-duplicates so OCR
        spam does not grow the file without bound.
        """
        if not name:
            return

        missing_file = DATA_DIR / "missing_items.csv"

        # Fail silently to not interrupt the user
        with suppress(Exception):
            seen: set[str] = set()
            unique: list[str] = []
            raw_count = 0
            if missing_file.is_file():
                with missing_file.open(newline="", encoding="utf-8") as f:
                    for row in csv.reader(f):
                        if not row or not row[0].strip():
                            continue
                        raw_count += 1
                        item = row[0].strip()
                        if item in seen:
                            continue
                        seen.add(item)
                        unique.append(item)

            if name not in seen:
                unique.append(name)
            elif raw_count == len(unique):
                return

            missing_file.parent.mkdir(parents=True, exist_ok=True)
            with missing_file.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, lineterminator="\n")
                for item in unique:
                    writer.writerow([item])

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> Item:
        """Convert database row to Item model."""
        return Item(
            name=row["name"],
            action=row["action"],
            recycle_for=row["recycle_for"],
            keep_for=row["keep_for"],
            sell_price=row["sell_price"],
            stack_size=row["stack_size"],
        )

    def get_all_items(self) -> list[Item]:
        """Get all items from the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT name, action, recycle_for, keep_for, sell_price, stack_size FROM items ORDER BY name"
            )
            return [
                Item(
                    name=row["name"],
                    action=row["action"],
                    recycle_for=row["recycle_for"],
                    keep_for=row["keep_for"],
                    sell_price=row["sell_price"],
                    stack_size=row["stack_size"],
                )
                for row in cursor.fetchall()
            ]

    def clear(self) -> None:
        """Delete all items from the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM items")
            conn.commit()


def get_database() -> Database:
    """Get a database instance."""
    return Database()


def load_csv_to_database(csv_path: Path | str, *, clear_existing: bool = True) -> int:
    """
    Convenience function to load a CSV file into the database.

    Args:
        csv_path: Path to CSV file (columns: name, action, recycle_for, keep_for, sell_price, stack_size)
        clear_existing: If True, clears existing data before loading

    Returns:
        Number of items loaded
    """
    db = get_database()
    return db.load_csv(Path(csv_path), clear_existing=clear_existing)
