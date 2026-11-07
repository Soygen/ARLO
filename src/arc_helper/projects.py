"""Structured wiki project catalog (tables in arlo.db / data/projects.csv)."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from typing import TYPE_CHECKING

from .config import APP_DIR

if TYPE_CHECKING:
    from pathlib import Path

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    cleaned = (value or "").lower().replace("'", "").replace("'", "")
    return _NON_ALNUM.sub("-", cleaned).strip("-")


def _norm_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _default_db_path() -> Path:
    return APP_DIR / "arlo.db"


def _parse_iso_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


@dataclass(frozen=True)
class ProjectRequirement:
    project_id: str
    project_name: str
    item_id: str
    item_name: str
    quantity: int


class ProjectCatalog:
    """Read-only access to the cached project requirements catalog."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path if db_path is not None else _default_db_path()
        self._schedule_cache: dict[str, date | None] | None = None
        self._name_to_id: dict[str, str] | None = None

    @property
    def available(self) -> bool:
        if not self.db_path.is_file():
            return False
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='projects'"
                ).fetchone()
                return row is not None
        except sqlite3.Error:
            return False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _has_schedule_columns(conn: sqlite3.Connection) -> bool:
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()
        }
        return "ends_on" in cols

    def _load_schedule(self) -> None:
        if self._schedule_cache is not None:
            return
        ends: dict[str, date | None] = {}
        names: dict[str, str] = {}
        if not self.available:
            self._schedule_cache = ends
            self._name_to_id = names
            return
        with self._connect() as conn:
            if self._has_schedule_columns(conn):
                rows = conn.execute("SELECT id, name, ends_on FROM projects").fetchall()
                for row in rows:
                    ends[row["id"]] = _parse_iso_date(row["ends_on"])
                    names[_norm_name(row["name"])] = row["id"]
            else:
                rows = conn.execute("SELECT id, name FROM projects").fetchall()
                for row in rows:
                    ends[row["id"]] = None
                    names[_norm_name(row["name"])] = row["id"]
        self._schedule_cache = ends
        self._name_to_id = names

    def is_active(
        self,
        project_id_or_name: str,
        *,
        today: date | None = None,
    ) -> bool:
        """True when the project has no end date or has not expired yet.

        Unknown labels (e.g. wiki-only ``Expedition 5``) fail open as active.
        """
        today = today or datetime.now(UTC).date()
        self._load_schedule()
        assert self._schedule_cache is not None
        assert self._name_to_id is not None
        key = (project_id_or_name or "").strip()
        if not key:
            return True
        pid = key if key in self._schedule_cache else None
        if pid is None:
            pid = self._name_to_id.get(_norm_name(key))
        if pid is None:
            # Prefix: "Expedition 5" → catalog id "expedition"
            compact = _norm_name(key)
            for name, cand in self._name_to_id.items():
                if compact == name or compact.startswith(name + " "):
                    pid = cand
                    break
        if pid is None:
            return True
        end = self._schedule_cache.get(pid)
        if end is None:
            return True
        return today <= end

    def count(self) -> int:
        if not self.available:
            return 0
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])

    def requirements_for_item(
        self,
        item_name: str,
        *,
        today: date | None = None,
        include_expired: bool = False,
    ) -> list[ProjectRequirement]:
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
        reqs = [
            ProjectRequirement(
                project_id=row["project_id"],
                project_name=row["project_name"],
                item_id=row["item_id"],
                item_name=row["item_name"],
                quantity=int(row["quantity"]),
            )
            for row in rows
        ]
        if include_expired:
            return reqs
        return [r for r in reqs if self.is_active(r.project_id, today=today)]

    def remaining_for_item(
        self,
        item_name: str,
        *,
        project_items: dict[str, dict[str, str]],
        today: date | None = None,
    ) -> list[ProjectRequirement]:
        """Drop expired projects and requirements marked COMPLETED."""
        remaining: list[ProjectRequirement] = []
        item_slug = _slugify(item_name)
        for req in self.requirements_for_item(item_name, today=today):
            bucket = project_items.get(_norm_name(req.project_name))
            # Only drop when this item is explicitly COMPLETED. Missing rows are
            # common for later project stages (MetaForge has not emitted them
            # yet) — those must stay as remaining need.
            if bucket and bucket.get(item_slug) == "COMPLETED":
                continue
            remaining.append(req)
        return remaining

    def total_needed(
        self,
        item_name: str,
        *,
        project_items: dict[str, dict[str, str]],
        today: date | None = None,
    ) -> int:
        return sum(
            r.quantity
            for r in self.remaining_for_item(
                item_name, project_items=project_items, today=today
            )
        )


def get_project_catalog(db_path: Path | None = None) -> ProjectCatalog | None:
    catalog = ProjectCatalog(db_path)
    return catalog if catalog.available else None
