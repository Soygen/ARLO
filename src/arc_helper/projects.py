"""Structured wiki project catalog (tables in data/arlo.db / projects.csv)."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import APP_DIR

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    cleaned = (value or "").lower().replace("'", "").replace("'", "")
    return _NON_ALNUM.sub("-", cleaned).strip("-")


def _norm_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _default_db_path() -> Path:
    return APP_DIR / "data" / "arlo.db"


@dataclass(frozen=True)
class ProjectRequirement:
    project_id: str
    project_name: str
    item_id: str
    item_name: str
    quantity: int


class ProjectCatalog:
    """Read-only access to the cached project requirements catalog."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path if db_path is not None else _default_db_path()

    @property
    def available(self) -> bool:
        if not self.db_path.is_file():
            return False
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='projects'"
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
            return int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])

    def requirements_for_item(self, item_name: str) -> list[ProjectRequirement]:
        if not self.available or not (item_name or "").strip():
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.project_id, p.name AS project_name,
                       r.item_id, r.item_name, r.quantity
                FROM project_requirements r
                JOIN projects p ON p.id = r.project_id
                WHERE r.item_name = ? COLLATE NOCASE
                ORDER BY p.name COLLATE NOCASE
                """,
                (item_name.strip(),),
            ).fetchall()
        return [
            ProjectRequirement(
                project_id=row["project_id"],
                project_name=row["project_name"],
                item_id=row["item_id"],
                item_name=row["item_name"],
                quantity=int(row["quantity"]),
            )
            for row in rows
        ]

    def remaining_for_item(
        self,
        item_name: str,
        *,
        project_items: dict[str, dict[str, str]],
    ) -> list[ProjectRequirement]:
        """Drop requirements whose mapped progress for this item is COMPLETED."""
        remaining: list[ProjectRequirement] = []
        item_slug = _slugify(item_name)
        for req in self.requirements_for_item(item_name):
            bucket = project_items.get(_norm_name(req.project_name))
            if bucket and bucket.get(item_slug) == "COMPLETED":
                continue
            # Project fully done and this item isn't a tracked leftover
            if (
                bucket
                and item_slug not in bucket
                and all(s == "COMPLETED" for s in bucket.values())
            ):
                continue
            remaining.append(req)
        return remaining

    def total_needed(
        self,
        item_name: str,
        *,
        project_items: dict[str, dict[str, str]],
    ) -> int:
        return sum(
            r.quantity
            for r in self.remaining_for_item(
                item_name, project_items=project_items
            )
        )


def get_project_catalog(db_path: Path | None = None) -> ProjectCatalog | None:
    catalog = ProjectCatalog(db_path)
    return catalog if catalog.available else None
