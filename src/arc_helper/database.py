"""
Database module for Arc Raiders Helper.
SQLite operations for item recommendations with Pydantic models.
"""

import csv
import difflib
import sqlite3
from pathlib import Path

from pydantic import BaseModel
from pydantic import Field

from .config import get_settings


class Item(BaseModel):
    """Item model matching database schema."""

    name: str = Field(..., min_length=1, max_length=200)
    action: str = Field(..., min_length=1, max_length=100)
    recycle_for: str | None = Field(default=None, max_length=500)
    keep_for: str | None = Field(default=None, max_length=500)
    sell_price: int | None = Field(default=None)
    stack_size: int | None = Field(default=None)


class Database:
    """SQLite database handler for items."""

    def __init__(self, db_path: Path | None = None):
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

            # If no exact match, try LIKE match
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

            # If still no match, try fuzzy matching (handles OCR typos)
            if not row:
                cursor = conn.execute("SELECT name FROM items")
                all_names = [r[0] for r in cursor.fetchall()]

                # Find closest match with at least 80% similarity
                matches = difflib.get_close_matches(
                    clean_name, all_names, n=1, cutoff=0.8
                )

                if matches:
                    match_name = matches[0]
                    cursor = conn.execute(
                        "SELECT * FROM items WHERE name = ?", (match_name,)
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

    def log_missing_item(self, name: str) -> None:
        """Log an unknown item to missing_items.csv for easy addition later."""
        if not name:
            return

        missing_file = self.db_path.parent / "missing_items.csv"

        try:
            # Append to file
            with missing_file.open("a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([name])
        except Exception:  # noqa: BLE001
            pass  # Fail silently to not interrupt the user

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> Item:
        """Convert database row to Item model."""
        return Item(
            name=row["name"],
            action=row["action"],
            recycle_for=row["recycle_for"],
            keep_for=row["keep_for"],
            sell_price=row["sell_price"] if "sell_price" in row.keys() else None,
            stack_size=row["stack_size"] if "stack_size" in row.keys() else None,
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
