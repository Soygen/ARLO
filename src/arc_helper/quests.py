"""Structured MetaForge quest catalog (tables in arlo.db / data/quests.csv)."""

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
class QuestRequirement:
    quest_id: str
    quest_name: str
    item_id: str
    item_name: str
    quantity: int


class QuestCatalog:
    """Read-only access to the cached quest catalog."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path if db_path is not None else _default_db_path()
        self._quest_ids: set[str] | None = None
        self._name_to_id: dict[str, str] | None = None

    @property
    def available(self) -> bool:
        if not self.db_path.is_file():
            return False
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quests'"
                ).fetchone()
                return row is not None
        except sqlite3.Error:
            return False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def count(self) -> int:
        if not self.available:
            return 0
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM quests").fetchone()[0])

    def _ensure_indexes(self) -> None:
        if self._quest_ids is not None and self._name_to_id is not None:
            return
        if not self.available:
            self._quest_ids = set()
            self._name_to_id = {}
            return
        with self._connect() as conn:
            rows = conn.execute("SELECT id, name FROM quests").fetchall()
        self._quest_ids = {r["id"] for r in rows}
        self._name_to_id = {r["name"].strip().lower(): r["id"] for r in rows}

    def has_quest(self, quest_id_or_name: str) -> bool:
        self._ensure_indexes()
        key = (quest_id_or_name or "").strip()
        if not key:
            return False
        assert self._quest_ids is not None
        assert self._name_to_id is not None
        if key in self._quest_ids:
            return True
        return key.lower() in self._name_to_id

    def resolve_quest_id(self, quest_id_or_name: str) -> str | None:
        self._ensure_indexes()
        key = (quest_id_or_name or "").strip()
        if not key:
            return None
        assert self._quest_ids is not None
        assert self._name_to_id is not None
        if key in self._quest_ids:
            return key
        return self._name_to_id.get(key.lower())

    def requirements_for_item(self, item_name: str) -> list[QuestRequirement]:
        """All catalog requirements for an item (by display name, case-insensitive)."""
        if not self.available or not (item_name or "").strip():
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.quest_id, q.name AS quest_name,
                       r.item_id, r.item_name, r.quantity
                FROM quest_requirements r
                JOIN quests q ON q.id = r.quest_id
                WHERE r.item_name = ? COLLATE NOCASE
                ORDER BY q.name COLLATE NOCASE
                """,
                (item_name.strip(),),
            ).fetchall()
        return [
            QuestRequirement(
                quest_id=row["quest_id"],
                quest_name=row["quest_name"],
                item_id=row["item_id"],
                item_name=row["item_name"],
                quantity=int(row["quantity"]),
            )
            for row in rows
        ]

    def remaining_for_item(
        self,
        item_name: str,
        completed_quests: set[str],
    ) -> list[QuestRequirement]:
        """Requirements for ``item_name`` whose quest is not completed."""
        done = {c.lower() for c in completed_quests}
        remaining: list[QuestRequirement] = []
        for req in self.requirements_for_item(item_name):
            if req.quest_id.lower() in done:
                continue
            remaining.append(req)
        return remaining

    def total_needed(
        self,
        item_name: str,
        completed_quests: set[str],
    ) -> int:
        return sum(
            r.quantity for r in self.remaining_for_item(item_name, completed_quests)
        )


def get_quest_catalog(db_path: Path | None = None) -> QuestCatalog | None:
    """Return a catalog if quest tables exist in arlo.db, else None."""
    catalog = QuestCatalog(db_path)
    return catalog if catalog.available else None
