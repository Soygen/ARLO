"""Map marker catalog and query helpers (Metaforge game-map-data)."""

from __future__ import annotations

import math
import re
import sqlite3
from datetime import UTC
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel
from pydantic import Field

from .config import APP_DIR
from .config import DATA_DIR
from .config import get_settings


class MapId(StrEnum):
    """Metaforge API ``mapID`` values (single source of truth for map keys)."""

    DAM = "dam"
    SPACEPORT = "spaceport"
    BURIED_CITY = "buried-city"
    BLUE_GATE = "blue-gate"
    STELLA_MONTIS = "stella-montis"
    RIVEN_TIDES = "riven-tides"
    PENDOLA_PASS = "pendola-pass"


# API mapID values that currently return markers (order = UI / ingest order)
KNOWN_MAP_IDS: tuple[MapId, ...] = tuple(MapId)

# Human labels for UI — keyed by MapId, never hardcode the slug in widgets.
MAP_DISPLAY_NAMES: dict[MapId, str] = {
    MapId.DAM: "Dam Battlegrounds",
    MapId.SPACEPORT: "Spaceport",
    MapId.BURIED_CITY: "Buried City",
    MapId.BLUE_GATE: "The Blue Gate",
    MapId.STELLA_MONTIS: "Stella Montis",
    MapId.RIVEN_TIDES: "Riven Tides",
    MapId.PENDOLA_PASS: "Pendola Pass",
}

# CDN path slug when it differs from API mapID
CDN_SLUGS: dict[MapId, str] = {
    MapId.RIVEN_TIDES: "riventides",
}


def coerce_map_id(map_id: str | MapId) -> MapId | None:
    """Parse a map id string into ``MapId``, or ``None`` if unknown."""
    try:
        return map_id if isinstance(map_id, MapId) else MapId(map_id)
    except ValueError:
        return None


def map_display_name(map_id: str | MapId) -> str:
    """UI label for a Metaforge map id (falls back to the raw id)."""
    mid = coerce_map_id(map_id)
    if mid is None:
        return str(map_id)
    return MAP_DISPLAY_NAMES.get(mid, str(mid))


CATEGORIES: tuple[str, ...] = (
    "containers",
    "nature",
    "arc",
    "events",
    "locations",
    "quests",
)

CATEGORY_COLORS: dict[str, str] = {
    "containers": "#5B9BD5",
    "nature": "#70AD47",
    "arc": "#ED7D31",
    "events": "#A64D79",
    "locations": "#FFC000",
    "quests": "#9B59B6",
    # Metaforge item-view "loose" spawn clusters (not community map markers)
    "loose": "#cc3099",
}

# Collapse Metaforge loose spawn points closer than this (world CRS units).
# ~40 keeps shelf aisles distinct without matching Metaforge's per-surface dots.
LOOSE_CLUSTER_RADIUS = 40.0

_MIN_ARC_TOKEN_LEN = 3
_MIN_CONTAINS_QUERY_LEN = 4

# Metaforge stores ARC enemies under internal subcategory ids (e.g. bison);
# display names come from /api/arc-raiders/arcs.
ARC_SUBCATEGORY_NAMES: dict[str, str] = {
    "arc-assessor": "ARC Assessor",
    "frigate": "ARC Frigate",
    "rollbot": "ARC Surveyor",
    "turbine": "ARC Turbine",
    "bastion": "Bastion",
    "bombardier": "Bombardier",
    "bully": "Bully",
    "comet": "Comet",
    "fireball": "Fireball",
    "firefly": "Firefly",
    "hornet": "Hornet",
    "hydra": "Hydra",
    "bison": "Leaper",
    "matriarch": "Matriarch",
    "pop": "Pop",
    "queen": "Queen",
    "rocketeer": "Rocketeer",
    "sentinel": "Sentinel",
    "shredder": "Shredder",
    "skulker": "Skulker",
    "snitch": "Snitch",
    "spotter": "Spotter",
    "tick": "Tick",
    "turret": "Turret",
    "vaporizer": "Vaporizer",
    "wasp": "Wasp",
}

# Metaforge item ``loot_area`` values that are drop *sources*, not map zones.
# Matching markers on these (e.g. random Fireball pins tagged ``ARC``) is noise.
NON_PLACE_LOOT_AREAS: frozenset[str] = frozenset({"arc"})

# Enemy-part items → related husk containers (subcategory ids).
ARC_RELATED_HUSKS: dict[str, tuple[str, ...]] = {
    "wasp": ("wasp_husk",),
}


def humanize_slug(slug: str) -> str:
    """``what-we-left-behind`` / ``a_first_foothold`` → title case."""
    s = (slug or "").strip()
    if not s:
        return ""
    if "-" in s or "_" in s:
        return " ".join(p.capitalize() for p in re.split(r"[-_]+", s) if p)
    return s


def marker_label(
    marker: MapMarker | None = None,
    *,
    subcategory: str = "",
    instance_name: str = "",
    category: str = "",
    loot_areas: str = "",
    with_loot: bool = False,
    quest_title: str | None = None,
) -> str:
    """Human-readable pin name (ARC aliases, then instance, subcategory, category)."""
    if marker is not None:
        subcategory = marker.subcategory
        instance_name = marker.instance_name
        category = marker.category
        loot_areas = marker.loot_areas
    inst = (instance_name or "").strip()
    sub = (subcategory or "").strip()
    cat = (category or "").strip()

    # Quest pins share one title; objective text is a subtitle when present.
    if cat == "quests" and sub:
        title = (quest_title or "").strip() or humanize_slug(sub)
        if inst:
            inst_l = inst.lower()
            title_l = title.lower()
            # Prefer the instance string when it already names the quest.
            if inst_l == title_l or inst_l == sub.lower() or title_l in inst_l:
                base = inst
            else:
                base = f"{title} — {inst}"
        else:
            base = title
        if with_loot and (loot_areas or "").strip():
            base = f"{base} ({loot_areas.strip()})"
        return base

    if inst:
        # Prefer a real instance label; still rewrite bare internal ARC ids.
        if inst.lower() == sub.lower() and sub in ARC_SUBCATEGORY_NAMES:
            base = ARC_SUBCATEGORY_NAMES[sub]
        else:
            base = inst
    elif sub in ARC_SUBCATEGORY_NAMES:
        base = ARC_SUBCATEGORY_NAMES[sub]
    else:
        base = humanize_slug(sub) if sub else ""
        if not base:
            base = cat or "pin"
    # Clarify rooms / open spots (not ARC aliases or plain containers)
    if cat in {"locations", "events", "nature"} and cat not in base.lower():
        pretty_sub = humanize_slug(sub) if ("-" in sub or "_" in sub) else sub
        if (
            pretty_sub
            and pretty_sub.lower() not in base.lower()
            and pretty_sub.lower() != cat
        ):
            base = f"{base} · {pretty_sub}"
        elif not inst and pretty_sub.lower() == base.lower():
            base = f"{base} · {cat}"
    if with_loot and (loot_areas or "").strip():
        base = f"{base} ({loot_areas.strip()})"
    return base


def expand_search_needles(search: str) -> list[str]:
    """Expand a user search into SQL/LIKE needles (ARC aliases, etc.)."""
    raw = (search or "").strip()
    if not raw:
        return []
    needles: list[str] = [raw]
    key = raw.lower()
    seen = {key}
    # Quest/item slugs: spaces ↔ hyphens ↔ underscores
    collapsed = re.sub(r"[-_\s]+", " ", key).strip()
    if collapsed and collapsed not in seen:
        needles.append(collapsed)
        seen.add(collapsed)
    for sep in ("-", "_"):
        variant = sep.join(collapsed.split()) if collapsed else ""
        if variant and variant not in seen:
            needles.append(variant)
            seen.add(variant)
    # Exact subcategory id typed
    if key in ARC_SUBCATEGORY_NAMES and ARC_SUBCATEGORY_NAMES[key].lower() not in seen:
        needles.append(ARC_SUBCATEGORY_NAMES[key])
        seen.add(ARC_SUBCATEGORY_NAMES[key].lower())
    # Display name → internal id (Leaper → bison). Prefix match so "leap" works.
    for sub_id, display in ARC_SUBCATEGORY_NAMES.items():
        d = display.lower()
        if key in {d, sub_id} or d.startswith(key) or key.startswith(d):
            if sub_id not in seen:
                needles.append(sub_id)
                seen.add(sub_id)
            if d not in seen:
                needles.append(display)
                seen.add(d)
    # Common shorthand not equal to the official display prefix
    if "surveyor" in key and "rollbot" not in seen:
        needles.append("rollbot")
    return needles


def place_loot_areas(areas: set[str] | list[str] | None) -> set[str]:
    """Loot tags usable as map zones (drops ``ARC`` source tags, etc.)."""
    out: set[str] = set()
    for raw in areas or ():
        a = (raw or "").strip()
        if a and a.casefold() not in NON_PLACE_LOOT_AREAS:
            out.add(a)
    return out


def arc_subcategories_for_item(item_name: str) -> list[str]:
    """Map an ARC part name to enemy subcategory ids (``Bully Fragmenter`` → ``bully``)."""
    key = (item_name or "").strip().lower()
    if not key:
        return []
    hits: list[str] = []
    seen: set[str] = set()
    # Longer display names first so ``ARC Surveyor`` beats bare ``ARC``.
    for sub_id, display in sorted(
        ARC_SUBCATEGORY_NAMES.items(), key=lambda kv: -len(kv[1])
    ):
        d = display.lower()
        bare = d.removeprefix("arc ").strip()
        tokens = [d, bare, sub_id.replace("-", " "), sub_id]
        if (
            any(t and len(t) >= _MIN_ARC_TOKEN_LEN and t in key for t in tokens)
            and sub_id not in seen
        ):
            seen.add(sub_id)
            hits.append(sub_id)
    if "leaper" in key and "bison" not in seen:
        hits.append("bison")
        seen.add("bison")
    if "surveyor" in key and "rollbot" not in seen:
        hits.append("rollbot")
    return hits


def maps_dir() -> Path:
    return DATA_DIR / "maps"


def map_dir(map_id: str) -> Path:
    return maps_dir() / map_id


def overview_path(map_id: str) -> Path:
    return map_dir(map_id) / "overview.webp"


def reference_path(map_id: str) -> Path:
    """Primary CV / companion basemap (prefer stitched zoom tiles)."""
    stitched = map_dir(map_id) / "reference.png"
    if stitched.is_file():
        return stitched
    z0 = map_dir(map_id) / "z0_0_0.webp"
    if z0.is_file():
        return z0
    return overview_path(map_id)


def cdn_slug(map_id: str | MapId) -> str:
    try:
        mid = MapId(str(map_id))
    except ValueError:
        return str(map_id)
    return CDN_SLUGS.get(mid, str(mid))


def default_db_path() -> Path:
    try:
        return get_settings().database_path
    except Exception:
        return APP_DIR / "arlo.db"


class MapMarker(BaseModel):
    """One Metaforge map pin."""

    id: str
    map_id: str
    lat: float
    lng: float
    category: str = ""
    subcategory: str = ""
    instance_name: str = ""
    loot_areas: str = ""
    behind_locked_door: bool = False
    zlayers: int = 0
    distance: float | None = Field(
        default=None,
        description="Distance to a query point when sorted nearby",
    )


class MapMeta(BaseModel):
    """Per-map bounds and cached asset paths."""

    map_id: str
    display_name: str = ""
    lat_min: float = 0.0
    lat_max: float = 1.0
    lng_min: float = 0.0
    lng_max: float = 1.0
    marker_count: int = 0
    overview_rel: str = ""
    reference_rel: str = ""
    ref_width: int = 0
    ref_height: int = 0


def ensure_map_schema(conn: sqlite3.Connection) -> None:
    """Create map tables if missing (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS map_markers (
            id TEXT NOT NULL,
            map_id TEXT NOT NULL,
            lat REAL NOT NULL,
            lng REAL NOT NULL,
            category TEXT,
            subcategory TEXT,
            instance_name TEXT,
            loot_areas TEXT,
            behind_locked_door INTEGER DEFAULT 0,
            zlayers INTEGER DEFAULT 0,
            PRIMARY KEY (id, map_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_map_markers_map ON map_markers(map_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_map_markers_cat "
        "ON map_markers(map_id, category)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS map_meta (
            map_id TEXT PRIMARY KEY NOT NULL,
            display_name TEXT,
            lat_min REAL,
            lat_max REAL,
            lng_min REAL,
            lng_max REAL,
            marker_count INTEGER,
            overview_rel TEXT,
            reference_rel TEXT,
            ref_width INTEGER,
            ref_height INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS item_loot_areas (
            name TEXT PRIMARY KEY NOT NULL COLLATE NOCASE,
            loot_areas TEXT NOT NULL,
            item_id TEXT
        )
        """
    )
    # Older DBs may lack item_id — add idempotently.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(item_loot_areas)").fetchall()}
    if "item_id" not in cols:
        conn.execute("ALTER TABLE item_loot_areas ADD COLUMN item_id TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS map_item_spot_clusters (
            map_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            lat REAL NOT NULL,
            lng REAL NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (map_id, item_id, lat, lng)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_item_spot_clusters_lookup "
        "ON map_item_spot_clusters(map_id, item_name COLLATE NOCASE)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS map_item_spots_meta (
            map_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            raw_count INTEGER NOT NULL DEFAULT 0,
            cluster_count INTEGER NOT NULL DEFAULT 0,
            fetched_at TEXT,
            PRIMARY KEY (map_id, item_id)
        )
        """
    )
    conn.commit()


class MapCatalog:
    """Read helpers for map markers + loot-area index."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else default_db_path()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        ensure_map_schema(conn)
        return conn

    def list_maps(self) -> list[MapMeta]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM map_meta ORDER BY display_name COLLATE NOCASE"
            ).fetchall()
        return [self._row_to_meta(r) for r in rows]

    def get_meta(self, map_id: str) -> MapMeta | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM map_meta WHERE map_id = ?", (map_id,)
            ).fetchone()
        return self._row_to_meta(row) if row else None

    def marker_count(self, map_id: str | None = None) -> int:
        with self._connect() as conn:
            if map_id:
                row = conn.execute(
                    "SELECT COUNT(*) FROM map_markers WHERE map_id = ?",
                    (map_id,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM map_markers").fetchone()
        return int(row[0]) if row else 0

    def query_markers(
        self,
        map_id: str,
        *,
        categories: set[str] | None = None,
        loot_areas: set[str] | None = None,
        subcategory: str | None = None,
        search: str | None = None,
        behind_locked_only: bool = False,
        origin: tuple[float, float] | None = None,
        limit: int | None = None,
    ) -> list[MapMarker]:
        """Filter markers; optionally sort by distance to ``origin`` (lat, lng)."""
        sql = [
            "SELECT * FROM map_markers WHERE map_id = ?",
        ]
        params: list[object] = [map_id]

        if categories:
            placeholders = ",".join("?" for _ in categories)
            sql.append(f"AND category IN ({placeholders})")
            params.extend(sorted(categories))

        if subcategory:
            sql.append("AND subcategory = ?")
            params.append(subcategory)

        if behind_locked_only:
            sql.append("AND behind_locked_door = 1")

        if loot_areas:
            # loot_areas stored as comma-separated; match any tag
            area_clauses = []
            for area in sorted(loot_areas):
                area_clauses.append(
                    "(loot_areas LIKE ? OR loot_areas LIKE ? OR loot_areas LIKE ? OR loot_areas = ?)"
                )
                params.extend(
                    [
                        f"{area},%",
                        f"%, {area},%",
                        f"%, {area}",
                        area,
                    ]
                )
            sql.append("AND (" + " OR ".join(area_clauses) + ")")

        if search:
            needles = expand_search_needles(search)
            needle_clauses = []
            for needle_raw in needles:
                needle = f"%{needle_raw}%"
                needle_clauses.append(
                    "(instance_name LIKE ? COLLATE NOCASE "
                    "OR subcategory LIKE ? COLLATE NOCASE "
                    "OR loot_areas LIKE ? COLLATE NOCASE)"
                )
                params.extend([needle, needle, needle])
            sql.append("AND (" + " OR ".join(needle_clauses) + ")")

        with self._connect() as conn:
            rows = conn.execute(" ".join(sql), params).fetchall()

        markers = [self._row_to_marker(r) for r in rows]

        if origin is not None:
            olat, olng = origin
            for m in markers:
                m.distance = math.hypot(m.lat - olat, m.lng - olng)
            markers.sort(key=lambda m: (m.distance is None, m.distance or 0.0))
        else:
            markers.sort(
                key=lambda m: (
                    m.category,
                    m.subcategory,
                    m.instance_name,
                    m.id,
                )
            )

        if limit is not None and limit >= 0:
            markers = markers[:limit]
        return markers

    def loot_areas_for_item(self, item_name: str) -> list[str]:
        """Return loot-area tags for an item name (case-insensitive)."""
        ref = self.resolve_item_ref(item_name, network=False)
        name = ref[1] if ref else item_name.strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT loot_areas FROM item_loot_areas WHERE name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
        if not row or not row["loot_areas"]:
            return []
        return parse_loot_areas(row["loot_areas"])

    def item_id_for_name(self, item_name: str) -> str | None:
        """Metaforge item slug for a display name, if known."""
        ref = self.resolve_item_ref(item_name, network=False)
        return ref[0] if ref else None

    def resolve_item_ref(
        self,
        query: str,
        *,
        network: bool = False,
    ) -> tuple[str, str] | None:
        """Map a user query to ``(item_id, canonical_name)``.

        Local resolution order: exact name → exact item_id → unique prefix →
        unique substring (len≥4). Optionally falls back to Metaforge
        ``/items?search=`` when ``network=True``.
        """
        q = (query or "").strip()
        if len(q) < 1:
            return None
        q_l = q.lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name, item_id FROM item_loot_areas WHERE name = ? COLLATE NOCASE",
                (q,),
            ).fetchone()
            if row:
                iid = (row["item_id"] or "").strip()
                if iid:
                    return iid, row["name"]

            row = conn.execute(
                "SELECT name, item_id FROM item_loot_areas WHERE item_id = ? COLLATE NOCASE",
                (q_l,),
            ).fetchone()
            if row and (row["item_id"] or "").strip():
                return row["item_id"].strip(), row["name"]

            prefix = conn.execute(
                """
                SELECT name, item_id FROM item_loot_areas
                WHERE name LIKE ? COLLATE NOCASE
                ORDER BY LENGTH(name) ASC, name COLLATE NOCASE
                LIMIT 3
                """,
                (f"{q}%",),
            ).fetchall()
            if len(prefix) == 1 and (prefix[0]["item_id"] or "").strip():
                return prefix[0]["item_id"].strip(), prefix[0]["name"]

            if len(q) >= _MIN_CONTAINS_QUERY_LEN:
                contains = conn.execute(
                    """
                    SELECT name, item_id FROM item_loot_areas
                    WHERE name LIKE ? COLLATE NOCASE
                    ORDER BY LENGTH(name) ASC, name COLLATE NOCASE
                    LIMIT 3
                    """,
                    (f"%{q}%",),
                ).fetchall()
                if len(contains) == 1 and (contains[0]["item_id"] or "").strip():
                    return contains[0]["item_id"].strip(), contains[0]["name"]

            # Already-cached loose spots under this typed name / slug
            from arc_helper.map_ingest import _slugify_item

            slug = _slugify_item(q)
            cached = conn.execute(
                """
                SELECT item_id, item_name FROM map_item_spots_meta
                WHERE item_id = ? OR item_name = ? COLLATE NOCASE
                ORDER BY raw_count DESC
                LIMIT 1
                """,
                (slug, q),
            ).fetchone()
            if cached and (cached["item_id"] or "").strip():
                return cached["item_id"].strip(), cached["item_name"]

        if network:
            from arc_helper.map_ingest import lookup_item_by_search

            try:
                hit = lookup_item_by_search(q)
            except Exception:
                hit = None
            if hit:
                item_id, name = hit
                # Upsert so later resolves are local.
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO item_loot_areas (name, loot_areas, item_id)
                        VALUES (?, '', ?)
                        ON CONFLICT(name) DO UPDATE SET
                            item_id=COALESCE(NULLIF(excluded.item_id, ''), item_id)
                        """,
                        (name, item_id),
                    )
                    conn.commit()
                return item_id, name
        return None

    def loose_clusters_for_item(
        self,
        map_id: str,
        item_name: str,
        *,
        origin: tuple[float, float] | None = None,
        item_id: str | None = None,
    ) -> list[MapMarker]:
        """Clustered Metaforge loose-loot spawn pins for an item on one map."""
        name = item_name.strip()
        from arc_helper.map_ingest import _slugify_item

        iid = (item_id or "").strip() or _slugify_item(name)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT map_id, item_id, item_name, lat, lng, count
                FROM map_item_spot_clusters
                WHERE map_id = ?
                  AND (
                    item_name = ? COLLATE NOCASE
                    OR item_id = ?
                    OR item_id = ?
                  )
                ORDER BY count DESC, lat, lng
                """,
                (map_id, name, iid, (item_id or "").strip() or iid),
            ).fetchall()
        # Deduplicate by lat/lng in case name+id both matched
        seen_xy: set[tuple[float, float]] = set()
        out: list[MapMarker] = []
        display = name
        for r in rows:
            xy = (float(r["lat"]), float(r["lng"]))
            if xy in seen_xy:
                continue
            seen_xy.add(xy)
            count = int(r["count"] or 1)
            label = display or r["item_name"]
            if count > 1:
                label = f"{label} ×{count}"
            m = MapMarker(
                id=f"loose:{r['item_id']}:{r['lat']:.1f}:{r['lng']:.1f}",
                map_id=r["map_id"],
                lat=xy[0],
                lng=xy[1],
                category="loose",
                subcategory="spawn",
                instance_name=label,
                loot_areas="",
            )
            if origin is not None:
                m.distance = math.hypot(m.lat - origin[0], m.lng - origin[1])
            out.append(m)
        if origin is not None:
            out.sort(key=lambda m: (m.distance is None, m.distance or 0.0))
        return out

    def ensure_loose_spots(self, map_id: str, item_name: str) -> bool:
        """Fetch + cache Metaforge loose spots for ``item_name`` if missing.

        Returns True when the cache was updated (caller may want to refresh UI).
        Safe to call from a worker thread. Refuses to cache empty results for
        unresolved / partial queries so typeahead typos don't poison the cache.
        """
        name = item_name.strip()
        if not name or not map_id:
            return False

        ref = self.resolve_item_ref(name, network=True)
        if ref is None:
            # Unknown item — don't slugify-and-cache empties for "insulation rol".
            return False
        item_id, canonical = ref

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT raw_count, item_name FROM map_item_spots_meta
                WHERE map_id = ? AND item_id = ?
                """,
                (map_id, item_id),
            ).fetchone()
            if row is not None:
                # Successful fetch already on disk
                if int(row["raw_count"] or 0) > 0:
                    return False
                # Confirmed empty for this canonical item on this map
                return False

        from datetime import datetime

        from arc_helper.map_ingest import cluster_loose_spots
        from arc_helper.map_ingest import fetch_item_loose_spots

        try:
            spots = fetch_item_loose_spots(map_id, item_id)
        except Exception:
            return False
        clusters = cluster_loose_spots(spots)
        fetched_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO map_item_spots_meta
                    (map_id, item_id, item_name, raw_count, cluster_count, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (map_id, item_id, canonical, len(spots), len(clusters), fetched_at),
            )
            conn.execute(
                "DELETE FROM map_item_spot_clusters WHERE map_id = ? AND item_id = ?",
                (map_id, item_id),
            )
            if clusters:
                conn.executemany(
                    """
                    INSERT INTO map_item_spot_clusters
                        (map_id, item_id, item_name, lat, lng, count)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (map_id, item_id, canonical, lat, lng, count)
                        for lat, lng, count in clusters
                    ],
                )
            conn.commit()
        return True

    def markers_for_item(
        self,
        map_id: str,
        item_name: str,
        *,
        origin: tuple[float, float] | None = None,
        limit: int | None = None,
    ) -> list[MapMarker]:
        """Pins where an item can appear.

        Includes:
        - Clustered Metaforge **loose spawn** points (precise shelves/tables/…)
        - Community markers whose place ``loot_areas`` intersect the item's tags
          (``ARC`` source tags are skipped — they are not map zones)
        - ARC enemy pins for parts named after that enemy (``Bully Fragmenter``)
        - Nature/quest/event pins whose name matches the item

        Works for items **with or without** Metaforge ``loot_area`` tags.
        """
        raw = item_name.strip()
        ref = self.resolve_item_ref(raw, network=False)
        item_id = ref[0] if ref else None
        canonical = ref[1] if ref else raw
        areas = place_loot_areas(self.loot_areas_for_item(canonical))
        out: list[MapMarker] = []
        seen: set[str] = set()

        # Prefer precise loose clusters when we have them (by name or id).
        for m in self.loose_clusters_for_item(
            map_id, canonical, origin=origin, item_id=item_id
        ):
            if m.id not in seen:
                seen.add(m.id)
                out.append(m)

        if areas:
            # Do not limit to containers — topside materials often sit in
            # rooms / open spots tagged with the same loot areas.
            for m in self.query_markers(
                map_id,
                loot_areas=areas,
                origin=origin,
            ):
                if m.id not in seen:
                    seen.add(m.id)
                    out.append(m)

        # Enemy-named ARC parts → that enemy's pins (not every ARC-tagged marker).
        for sub in arc_subcategories_for_item(canonical):
            for m in self.query_markers(
                map_id,
                categories={"arc"},
                subcategory=sub,
                origin=origin,
            ):
                if m.id not in seen:
                    seen.add(m.id)
                    out.append(m)
            for husk in ARC_RELATED_HUSKS.get(sub, ()):
                for m in self.query_markers(
                    map_id,
                    categories={"containers"},
                    subcategory=husk,
                    origin=origin,
                ):
                    if m.id not in seen:
                        seen.add(m.id)
                        out.append(m)

        # Exact-ish name match (moss, candleberries, quest props, …)
        needle = canonical.lower()
        compact = needle.replace(" ", "").replace("-", "").replace("_", "")
        for m in self.query_markers(
            map_id,
            categories={"nature", "quests", "events", "locations"},
            search=canonical,
            origin=origin,
        ):
            hay = (
                f"{m.subcategory} {m.instance_name}".lower()
                .replace("_", " ")
                .replace("-", " ")
            )
            hay_c = hay.replace(" ", "")
            if (needle in hay or compact in hay_c) and m.id not in seen:
                seen.add(m.id)
                out.append(m)

        if origin is not None:
            olat, olng = origin
            for m in out:
                if m.distance is None:
                    m.distance = math.hypot(m.lat - olat, m.lng - olng)
            out.sort(key=lambda m: (m.distance is None, m.distance or 0.0))
        else:
            # Prefer loose clusters + labeled rooms over anonymous containers
            def _rank(m: MapMarker) -> tuple:
                labeled = 0 if (m.instance_name or "").strip() else 1
                cat_order = {
                    "loose": 0,
                    "locations": 1,
                    "nature": 2,
                    "events": 3,
                    "containers": 4,
                    "quests": 5,
                    "arc": 6,
                }.get(m.category, 9)
                return (cat_order, labeled, m.subcategory, m.instance_name, m.id)

            out.sort(key=_rank)

        if limit is not None and limit >= 0:
            out = out[:limit]
        return out

    def search_suggestions(self, query: str, *, limit: int = 25) -> list[str]:
        """Typeahead: all known Metaforge items + marker instance/subcategory."""
        q = (query or "").strip()
        if len(q) < 1:
            return []
        needle = f"%{q}%"
        names: list[str] = []
        seen: set[str] = set()
        with self._connect() as conn:
            # Prefer prefix matches, then contains — items without loot tags included.
            for sql in (
                (
                    "SELECT name FROM item_loot_areas "
                    "WHERE name LIKE ? COLLATE NOCASE "
                    "ORDER BY LENGTH(name) ASC, name COLLATE NOCASE LIMIT ?"
                ),
                (
                    "SELECT name FROM item_loot_areas "
                    "WHERE name LIKE ? COLLATE NOCASE "
                    "AND name NOT LIKE ? COLLATE NOCASE "
                    "ORDER BY name COLLATE NOCASE LIMIT ?"
                ),
            ):
                if "NOT LIKE" in sql:
                    rows = conn.execute(sql, (needle, f"{q}%", limit)).fetchall()
                else:
                    rows = conn.execute(sql, (f"{q}%", limit)).fetchall()
                for row in rows:
                    n = row["name"]
                    if n.lower() not in seen:
                        seen.add(n.lower())
                        names.append(n)
            for row in conn.execute(
                "SELECT DISTINCT instance_name AS n FROM map_markers "
                "WHERE instance_name LIKE ? COLLATE NOCASE "
                "AND instance_name != '' "
                "ORDER BY n COLLATE NOCASE LIMIT ?",
                (needle, limit),
            ):
                n = (row["n"] or "").strip()
                if n and n.lower() not in seen:
                    seen.add(n.lower())
                    names.append(n)
            for row in conn.execute(
                "SELECT DISTINCT subcategory AS n FROM map_markers "
                "WHERE subcategory LIKE ? COLLATE NOCASE "
                "AND subcategory != '' "
                "ORDER BY n COLLATE NOCASE LIMIT ?",
                (needle, limit),
            ):
                n = (row["n"] or "").strip().replace("_", " ").replace("-", " ")
                if n and n.lower() not in seen:
                    seen.add(n.lower())
                    names.append(n)
        return names[:limit]

    @staticmethod
    def _row_to_marker(row: sqlite3.Row) -> MapMarker:
        return MapMarker(
            id=row["id"],
            map_id=row["map_id"],
            lat=float(row["lat"]),
            lng=float(row["lng"]),
            category=row["category"] or "",
            subcategory=row["subcategory"] or "",
            instance_name=row["instance_name"] or "",
            loot_areas=row["loot_areas"] or "",
            behind_locked_door=bool(row["behind_locked_door"]),
            zlayers=int(row["zlayers"] or 0),
        )

    @staticmethod
    def _row_to_meta(row: sqlite3.Row) -> MapMeta:
        mid = row["map_id"]
        return MapMeta(
            map_id=mid,
            display_name=row["display_name"] or map_display_name(mid),
            lat_min=float(row["lat_min"] or 0),
            lat_max=float(row["lat_max"] or 1),
            lng_min=float(row["lng_min"] or 0),
            lng_max=float(row["lng_max"] or 1),
            marker_count=int(row["marker_count"] or 0),
            overview_rel=row["overview_rel"] or "",
            reference_rel=row["reference_rel"] or "",
            ref_width=int(row["ref_width"] or 0),
            ref_height=int(row["ref_height"] or 0),
        )


def parse_loot_areas(raw: str | list | None) -> list[str]:
    """Normalize Metaforge lootAreas field to a list of tags."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    if not text:
        return []
    return [p.strip() for p in text.split(",") if p.strip()]


def _map_crs_south_up(map_id: str) -> bool:
    """Per-map lat→image Y rule (lazy import avoids map_ingest cycle)."""
    from arc_helper.map_ingest import MAP_TILE_CONFIGS

    mid = coerce_map_id(map_id)
    cfg = MAP_TILE_CONFIGS.get(mid) if mid is not None else None
    return bool(cfg and cfg.crs_south_up)


def latlng_to_norm(
    lat: float,
    lng: float,
    meta: MapMeta,
) -> tuple[float, float]:
    """Map Metaforge lat/lng → normalized (x, y) in 0..1 (y=0 at top of image).

    Higher ``lat`` is further south on ARC maps. Stitched basemaps are north-up
    (increasing lat → +y) via Metaforge's tileExtent affine. Maps with
    ``MapTileConfig.crs_south_up`` flip that (increasing lat → −y) only when a
    layer's tiles are inverted relative to Leaflet.
    """
    lat_span = meta.lat_max - meta.lat_min or 1.0
    lng_span = meta.lng_max - meta.lng_min or 1.0
    x = (lng - meta.lng_min) / lng_span
    if _map_crs_south_up(meta.map_id):
        y = (meta.lat_max - lat) / lat_span
    else:
        y = (lat - meta.lat_min) / lat_span
    return x, y


def latlng_on_reference(
    lat: float,
    lng: float,
    meta: MapMeta,
    *,
    margin: float = 0.0,
) -> bool:
    """True when ``(lat, lng)`` projects onto the stitched basemap (norm in 0..1).

    Metaforge marker dumps often include points past ``worldExtent`` / the
    published tile pyramid; those must not be drawn in the black void around
    the reference PNG (they expand the F8 scene and look like misplaced pins).
    """
    nx, ny = latlng_to_norm(lat, lng, meta)
    return -margin <= nx <= 1.0 + margin and -margin <= ny <= 1.0 + margin


def norm_to_latlng(
    x: float,
    y: float,
    meta: MapMeta,
) -> tuple[float, float]:
    """Inverse of ``latlng_to_norm``."""
    lat_span = meta.lat_max - meta.lat_min or 1.0
    lng_span = meta.lng_max - meta.lng_min or 1.0
    lng = meta.lng_min + x * lng_span
    if _map_crs_south_up(meta.map_id):
        lat = meta.lat_max - y * lat_span
    else:
        lat = meta.lat_min + y * lat_span
    return lat, lng


def get_map_catalog(db_path: Path | None = None) -> MapCatalog | None:
    """Return a catalog if the DB exists; empty tables are still OK."""
    path = Path(db_path) if db_path else default_db_path()
    if not path.is_file():
        return None
    return MapCatalog(path)
