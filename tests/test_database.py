"""Tests for database module."""

from pathlib import Path

from arc_helper.database import Database
from arc_helper.database import Item


class TestItem:
    """Tests for Item model."""

    def test_item_with_sell_price(self):
        """Item model includes sell_price field."""
        item = Item(
            name="Test Item",
            action="Keep",
            recycle_for="2x Metal",
            keep_for="5x for upgrade",
            sell_price="1000",
        )
        assert item.sell_price == "1000"

    def test_item_without_sell_price(self):
        """Item model works without sell_price."""
        item = Item(
            name="Test Item",
            action="Sell",
            recycle_for=None,
            keep_for=None,
        )
        assert item.sell_price is None


class TestDatabase:
    """Tests for Database class."""

    def test_init_creates_schema(self, temp_db_path: Path):
        """Database initialization creates schema with sell_price column."""
        db = Database(temp_db_path)
        assert temp_db_path.exists()
        assert db.count() == 0

    def test_load_csv_with_sell_price(self, temp_db_path: Path, sample_csv_file: Path):
        """CSV loading includes sell_price."""
        db = Database(temp_db_path)
        count = db.load_csv(sample_csv_file)
        assert count == 3

        item = db.lookup("Test Item")
        assert item is not None
        assert item.sell_price == "1000"

    def test_lookup_exact_match(self, temp_db_path: Path, sample_csv_file: Path):
        """Lookup finds exact match."""
        db = Database(temp_db_path)
        db.load_csv(sample_csv_file)

        item = db.lookup("Sell Item")
        assert item is not None
        assert item.action == "Sell"
        assert item.sell_price == "500"

    def test_lookup_case_insensitive(self, temp_db_path: Path, sample_csv_file: Path):
        """Lookup is case-insensitive."""
        db = Database(temp_db_path)
        db.load_csv(sample_csv_file)

        item = db.lookup("sell item")
        assert item is not None
        assert item.name == "Sell Item"

    def test_lookup_not_found(self, temp_db_path: Path, sample_csv_file: Path):
        """Lookup returns None for unknown items."""
        db = Database(temp_db_path)
        db.load_csv(sample_csv_file)

        item = db.lookup("Unknown Item XYZ")
        assert item is None

    def test_get_all_items(self, temp_db_path: Path, sample_csv_file: Path):
        """Get all items returns list with sell_price."""
        db = Database(temp_db_path)
        db.load_csv(sample_csv_file)

        items = db.get_all_items()
        assert len(items) == 3
        assert all(isinstance(i, Item) for i in items)
        assert any(i.sell_price == "1000" for i in items)

    def test_clear(self, temp_db_path: Path, sample_csv_file: Path):
        """Clear removes all items."""
        db = Database(temp_db_path)
        db.load_csv(sample_csv_file)
        assert db.count() == 3

        db.clear()
        assert db.count() == 0

    def test_csv_without_sell_price(self, temp_db_path: Path, tmp_path: Path):
        """CSV without sell_price column loads successfully."""
        csv_content = """name,action,recycle_for,keep_for
Old Item,Keep,2x Metal,5x for upgrade
"""
        csv_path = tmp_path / "old_format.csv"
        csv_path.write_text(csv_content, encoding="utf-8")

        db = Database(temp_db_path)
        count = db.load_csv(csv_path)
        assert count == 1

        item = db.lookup("Old Item")
        assert item is not None
        assert item.sell_price is None

    def test_migration_adds_sell_price_column(self, tmp_path: Path):
        """Migration adds sell_price column to existing DB."""
        import sqlite3

        db_path = tmp_path / "old.db"

        # Create old schema without sell_price
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE items (
                    name TEXT PRIMARY KEY NOT NULL COLLATE NOCASE,
                    action TEXT NOT NULL,
                    recycle_for TEXT,
                    keep_for TEXT
                )
            """)
            conn.execute(
                "INSERT INTO items (name, action) VALUES (?, ?)",
                ("Legacy Item", "Sell"),
            )
            conn.commit()

        # Open with Database class - should migrate
        db = Database(db_path)
        item = db.lookup("Legacy Item")
        assert item is not None
        assert item.sell_price is None  # NULL after migration
