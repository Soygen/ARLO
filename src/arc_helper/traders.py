"""Structured wiki trader inventories (tables in arlo.db / data/traders.csv)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .config import APP_DIR

if TYPE_CHECKING:
    from pathlib import Path


def _default_db_path() -> Path:
    return APP_DIR / "arlo.db"


@dataclass(frozen=True)
class TraderOffer:
    trader_id: str
    trader_name: str
    item_id: str
    item_name: str
    price: str | None


class TraderCatalog:
    """Read-only access to cached trader shop / barter inventories."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path if db_path is not None else _default_db_path()

    @property
    def available(self) -> bool:
        if not self.db_path.is_file():
            return False
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='traders'"
                ).fetchone()
                return row is not None
        except sqlite3.Error:
            return False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def sellers_for_item(self, item_name: str) -> list[TraderOffer]:
        if not self.available or not (item_name or "").strip():
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT i.trader_id, t.name AS trader_name,
                       i.item_id, i.item_name, i.price
                FROM trader_items i
                JOIN traders t ON t.id = i.trader_id
                WHERE i.item_name = ? COLLATE NOCASE
                ORDER BY t.name COLLATE NOCASE
                """,
                (item_name.strip(),),
            ).fetchall()
        return [
            TraderOffer(
                trader_id=row["trader_id"],
                trader_name=row["trader_name"],
                item_id=row["item_id"],
                item_name=row["item_name"],
                price=row["price"],
            )
            for row in rows
        ]

    def buy_hint(self, item_name: str) -> str | None:
        """Short overlay text like ``Celeste (1)`` / ``Tian Wen (900)``."""
        offers = self.sellers_for_item(item_name)
        if not offers:
            return None
        parts: list[str] = []
        for offer in offers:
            if offer.price:
                parts.append(f"{offer.trader_name} ({offer.price})")
            else:
                parts.append(offer.trader_name)
        return ", ".join(parts)


def get_trader_catalog(db_path: Path | None = None) -> TraderCatalog | None:
    catalog = TraderCatalog(db_path)
    return catalog if catalog.available else None
