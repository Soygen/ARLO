"""Fetch Metaforge map markers + basemap assets into arlo.db / data/maps/."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from typing import cast

import requests
from PIL import Image

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

from arc_helper.map_data import KNOWN_MAP_IDS
from arc_helper.map_data import LOOSE_CLUSTER_RADIUS
from arc_helper.map_data import MapId
from arc_helper.map_data import cdn_slug
from arc_helper.map_data import coerce_map_id
from arc_helper.map_data import ensure_map_schema
from arc_helper.map_data import map_dir
from arc_helper.map_data import map_display_name
from arc_helper.map_data import maps_dir

METAFORGE_API_BASE = "https://metaforge.app/api/arc-raiders"
METAFORGE_MAP_DATA_URL = "https://metaforge.app/api/game-map-data"
METAFORGE_SITE = "https://metaforge.app"
# Interactive map tiles live on the static host (see Metaforge MapConfigs.tileLayers).
STATIC_BASE = "https://static.metaforge.app/arc-raiders"
CDN_BASE = "https://cdn.metaforge.app/arc-raiders"
USER_AGENT = "ARLO/1.0 (Arc Raiders Loot Overlay - https://github.com/Soygen/ARLO)"

_HTTP_OK = 200
_MIN_PAIR = 2
_MIN_POOL_REF_LIST_LEN = 5
_REFERENCE_PNG = "reference.png"

# Overview thumbnails on static host use PascalCase + "Tile" (from the map picker).
_STATIC_UI_TILES: dict[MapId, str] = {
    MapId.BLUE_GATE: "BlueGateTile.webp",
    MapId.BURIED_CITY: "BuriedCityTile.webp",
    MapId.DAM: "DamBattlegroundsTile.webp",
    MapId.PENDOLA_PASS: "PendolaPassTile.webp",
    MapId.RIVEN_TIDES: "RivenTidesTile.webp",
    MapId.SPACEPORT: "SpaceportTile.webp",
    MapId.STELLA_MONTIS: "StellaTile.webp",
}


# Tile filename under …/{z}/ — try nested dirs first, then underscore (Stella).
TILE_NAME_FMTS: tuple[str, ...] = ("{x}/{y}.webp", "{x}_{y}.webp")


@dataclass(frozen=True)
class MapTileConfig:
    """Surface basemap tile layer from Metaforge MapConfigs."""

    # Path under /arc-raiders/maps/ — e.g. "blue-gate/20260112-q85"
    layer_path: str
    tile_size: int = 512
    # Prefer this zoom for the stitched reference (≈2–4k px).
    zoom: int = 2
    # Metaforge worldExtent as [lat0, lng0, lat1, lng1] (marker CRS corners).
    world_extent: tuple[float, float, float, float] = (0.0, 0.0, 16384.0, 16384.0)
    # Metaforge tileExtent [lat0, lng0, lat1, lng1] in Leaflet zoom-0 CRS units.
    # worldExtent maps onto this rectangle via an axis-aligned affine (Br);
    # tiles live in zoom-0 space [0, tile_size). Identity maps use
    # (0, 0, tile_size, tile_size).
    tile_extent: tuple[float, float, float, float] | None = None
    # Optional Metaforge tileBounds (same corner layout) when tiles are sparse.
    tile_bounds: tuple[float, float, float, float] | None = None
    # Optional scan cap when discovering sparse grids (Pendola).
    max_scan: int = 16
    # When True, image y=0 is high lat (south). Metaforge's affine uses a
    # positive scaleY (north-up CDN tiles); leave False unless a layer is flipped.
    crs_south_up: bool = False


# Extracted from Metaforge MapConfigs (surface / default layer).
MAP_TILE_CONFIGS: dict[MapId, MapTileConfig] = {
    MapId.DAM: MapTileConfig(
        layer_path=f"{MapId.DAM}/20260112-q85",
        # Metaforge logical tileSize is 256; CDN ships 512px retina tiles.
        tile_size=256,
        zoom=3,
        world_extent=(
            1634.8691424096007,
            5260.799310066582,
            3292.914501442535,
            2848.1510404315172,
        ),
        tile_extent=(
            1509.4971770749603 / 32,
            4864.125 / 32,
            3048.3572746351233 / 32,
            2625.3233572578824 / 32,
        ),
    ),
    MapId.SPACEPORT: MapTileConfig(
        layer_path=f"{MapId.SPACEPORT}/20260114-q85",
        tile_size=512,
        zoom=2,
        world_extent=(
            1781.4843302764823,
            2424.75,
            3421.448788892414,
            5104.75,
        ),
        tile_extent=(
            3416.484711417113 / 32,
            4478.25 / 32,
            6437.712566726013 / 32,
            9413.25 / 32,
        ),
    ),
    MapId.BURIED_CITY: MapTileConfig(
        layer_path=f"{MapId.BURIED_CITY}/20260112-q85",
        tile_size=512,
        zoom=2,
        world_extent=(0.0, 0.0, 16384.0, 16384.0),
        tile_extent=(0.0, 0.0, 512.0, 512.0),
    ),
    MapId.BLUE_GATE: MapTileConfig(
        layer_path=f"{MapId.BLUE_GATE}/20260112-q85",
        tile_size=512,
        zoom=2,
        world_extent=(
            7416.0,
            6553.450942288329,
            3439.2663383876925,
            9597.991683705897,
        ),
        tile_extent=(
            7456.756734878541 / 32,
            6538.616405632005 / 32,
            3438.0041235046288 / 32,
            9613.10436501387 / 32,
        ),
    ),
    MapId.STELLA_MONTIS: MapTileConfig(
        layer_path=f"{MapId.STELLA_MONTIS}/v3/default",
        tile_size=256,
        zoom=3,
        world_extent=(3412.0, 2206.0, 1362.0, 4874.0),
        tile_extent=(3599 / 32, 2071 / 32, 1288 / 32, 5081 / 32),
    ),
    MapId.RIVEN_TIDES: MapTileConfig(
        layer_path=f"{MapId.RIVEN_TIDES}/20260428-q85",
        tile_size=512,
        zoom=2,
        world_extent=(0.0, 0.0, 16384.0, 16384.0),
        tile_extent=(0.0, 0.0, 512.0, 512.0),
    ),
    MapId.PENDOLA_PASS: MapTileConfig(
        layer_path=f"{MapId.PENDOLA_PASS}/20261005b-satellite",
        tile_size=512,
        zoom=3,
        world_extent=(0.0, 0.0, 16384.0, 16384.0),
        tile_extent=(0.0, 0.0, 512.0, 512.0),
        tile_bounds=(3162.0, 626.0, 13220.0, 15758.0),
        max_scan=16,
    ),
}


def world_extent_bounds(
    extent: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Convert Metaforge ``worldExtent`` [lat0,lng0,lat1,lng1] → lat/lng min/max."""
    lat0, lng0, lat1, lng1 = extent
    return (
        min(lat0, lat1),
        max(lat0, lat1),
        min(lng0, lng1),
        max(lng0, lng1),
    )


def _resolved_tile_extent(
    cfg: MapTileConfig,
) -> tuple[float, float, float, float]:
    """Zoom-0 CRS rectangle that ``world_extent`` maps onto (Metaforge tileExtent)."""
    if cfg.tile_extent is not None:
        return cfg.tile_extent
    ts = float(cfg.tile_size)
    return (0.0, 0.0, ts, ts)


def extent_affine(
    world_extent: tuple[float, float, float, float],
    tile_extent: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Metaforge Br(): map world lat/lng → zoom-0 tile CRS.

    Returns ``(scale_x, offset_x, scale_y, offset_y)`` where
    ``tile_x = scale_x * lng + offset_x`` and ``tile_y = scale_y * lat + offset_y``.
    Corner layout matches MapConfigs: extent[lat0, lng0, lat1, lng1].
    """
    we_lat0, we_lng0, we_lat1, we_lng1 = world_extent
    te_lat0, te_lng0, te_lat1, te_lng1 = tile_extent
    scale_x = (te_lng1 - te_lng0) / (we_lng1 - we_lng0)
    offset_x = te_lng0 - scale_x * we_lng0
    scale_y = (te_lat1 - te_lat0) / (we_lat1 - we_lat0)
    offset_y = te_lat0 - scale_y * we_lat0
    return scale_x, offset_x, scale_y, offset_y


def tile_crs_to_latlng(
    cfg: MapTileConfig,
    tile_x: float,
    tile_y: float,
) -> tuple[float, float]:
    """Inverse of Metaforge's world→tile affine (zoom-0 CRS → marker lat/lng)."""
    scale_x, offset_x, scale_y, offset_y = extent_affine(
        cfg.world_extent, _resolved_tile_extent(cfg)
    )
    lng = (tile_x - offset_x) / scale_x
    lat = (tile_y - offset_y) / scale_y
    return lat, lng


def latlng_to_tile_crs(
    cfg: MapTileConfig,
    lat: float,
    lng: float,
) -> tuple[float, float]:
    """Forward Metaforge world→tile affine (marker lat/lng → zoom-0 CRS)."""
    scale_x, offset_x, scale_y, offset_y = extent_affine(
        cfg.world_extent, _resolved_tile_extent(cfg)
    )
    return scale_x * lng + offset_x, scale_y * lat + offset_y


def tile_grid_crs_bounds(
    cfg: MapTileConfig,
    min_x: int,
    max_x: int,
    min_y: int,
    max_y: int,
) -> tuple[float, float, float, float]:
    """CRS lat/lng coverage of a stitched zoom-tile index range.

    Metaforge does **not** stretch ``worldExtent`` across the full ``2^z`` grid.
    Markers use world lat/lng; Leaflet places tiles in zoom-0 CRS space via an
    affine onto ``tileExtent``. Tile ``(tx, ty)`` at zoom ``z`` covers
    ``[tx, tx+1) * tile_size / 2^z`` in that zoom-0 space. Positive ``scale_y``
    puts low lat (north) at small tile-y → top of the PNG.
    """
    scale = max(1, 2**cfg.zoom)
    ts = float(cfg.tile_size)
    tcrs_x0 = min_x * ts / scale
    tcrs_x1 = (max_x + 1) * ts / scale
    tcrs_y0 = min_y * ts / scale
    tcrs_y1 = (max_y + 1) * ts / scale
    corners = [
        tile_crs_to_latlng(cfg, tx, ty)
        for tx in (tcrs_x0, tcrs_x1)
        for ty in (tcrs_y0, tcrs_y1)
    ]
    lats = [c[0] for c in corners]
    lngs = [c[1] for c in corners]
    return min(lats), max(lats), min(lngs), max(lngs)


def _content_bbox(
    im: Image.Image, *, threshold: int = 8
) -> tuple[int, int, int, int] | None:
    """Inclusive pixel bbox of non-near-black content, or None if empty."""
    rgba = im.convert("RGBA")
    rgba.getchannel("A")
    # Luma-ish max channel keeps dark terrain; ignore fully transparent.
    rgb = rgba.convert("RGB")
    extrema = rgb.getextrema()
    if not extrema:
        return None
    # Build a mask via point ops would be heavy; use numpy-free scan of resized probe.
    probe = rgb.resize(
        (min(512, rgb.size[0]), min(512, rgb.size[1])),
        Image.Resampling.BOX,
    )
    px = probe.load()
    assert px is not None
    w, h = probe.size
    sx = rgb.size[0] / w
    sy = rgb.size[1] / h
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            r, g, b = cast("tuple[int, int, int]", px[x, y])
            if r > threshold or g > threshold or b > threshold:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    if max_x < 0:
        return None
    left = max(0, int(min_x * sx))
    top = max(0, int(min_y * sy))
    right = min(rgb.size[0] - 1, int((max_x + 1) * sx) - 1)
    bottom = min(rgb.size[1] - 1, int((max_y + 1) * sy) - 1)
    return left, top, right, bottom


def crop_reference_to_content(
    im: Image.Image,
    bounds: tuple[float, float, float, float],
    *,
    threshold: int = 8,
    south_up: bool = False,
) -> tuple[Image.Image, tuple[float, float, float, float]]:
    """Drop empty black margins and remap CRS bounds to the kept pixels.

    Metaforge tile pyramids only paint part of ``worldExtent`` (often ~60% of
    the square, Stella much less). Projecting onto the full black canvas makes
    pins float in the void around the art. Crop to painted pixels and shrink
    the CRS to match — same idea as per-map tile path / ``crs_south_up`` rules.
    """
    box = _content_bbox(im, threshold=threshold)
    if box is None:
        return im, bounds
    left, top, right, bottom = box
    w, h = im.size
    # Reject empty / noise crops only — Stella legitimately paints << 60%.
    if (right - left + 1) < w * 0.05 or (bottom - top + 1) < h * 0.05:
        return im, bounds
    if left == 0 and top == 0 and right == w - 1 and bottom == h - 1:
        return im, bounds
    lat_min, lat_max, lng_min, lng_max = bounds
    lat_span = lat_max - lat_min
    lng_span = lng_max - lng_min
    if south_up:
        # Image y=0 is lat_max (Leaflet CRS.Simple / CDN south-up).
        new_lat_max = lat_max - (top / h) * lat_span
        new_lat_min = lat_max - ((bottom + 1) / h) * lat_span
    else:
        new_lat_min = lat_min + (top / h) * lat_span
        new_lat_max = lat_min + ((bottom + 1) / h) * lat_span
    new_bounds = (
        min(new_lat_min, new_lat_max),
        max(new_lat_min, new_lat_max),
        lng_min + (left / w) * lng_span,
        lng_min + ((right + 1) / w) * lng_span,
    )
    cropped = im.crop((left, top, right + 1, bottom + 1))
    return cropped, new_bounds


def reference_bounds_path(map_id: str) -> Path:
    return map_dir(map_id) / "reference_bounds.json"


def write_reference_bounds(
    map_id: str, bounds: tuple[float, float, float, float]
) -> None:
    lat_min, lat_max, lng_min, lng_max = bounds
    path = reference_bounds_path(map_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "lat_min": lat_min,
                "lat_max": lat_max,
                "lng_min": lng_min,
                "lng_max": lng_max,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def read_reference_bounds(
    map_id: str,
) -> tuple[float, float, float, float] | None:
    path = reference_bounds_path(map_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (
            float(data["lat_min"]),
            float(data["lat_max"]),
            float(data["lng_min"]),
            float(data["lng_max"]),
        )
    except (OSError, KeyError, TypeError, ValueError):
        return None


def infer_reference_bounds_from_tiles(
    map_id: str, cfg: MapTileConfig
) -> tuple[float, float, float, float] | None:
    """Rebuild CRS bounds from an on-disk ``tiles_z*`` cache."""
    tile_dir = map_dir(map_id) / f"tiles_z{cfg.zoom}"
    if not tile_dir.is_dir():
        return None
    xs: list[int] = []
    ys: list[int] = []
    for p in tile_dir.glob("*.webp"):
        parts = p.stem.split("_")
        if len(parts) != _MIN_PAIR:
            continue
        try:
            xs.append(int(parts[0]))
            ys.append(int(parts[1]))
        except ValueError:
            continue
    if not xs or not ys:
        return None
    return tile_grid_crs_bounds(cfg, min(xs), max(xs), min(ys), max(ys))


def manifest_path() -> Path:
    return maps_dir() / "MANIFEST.json"


def tile_config_signature(cfg: MapTileConfig) -> dict[str, object]:
    """Stable fields that mean basemap tiles must be re-stitched when they change."""
    return {
        "layer_path": cfg.layer_path,
        "zoom": cfg.zoom,
        "tile_size": cfg.tile_size,
        "world_extent": list(cfg.world_extent),
        "tile_extent": list(_resolved_tile_extent(cfg)),
        "tile_bounds": list(cfg.tile_bounds) if cfg.tile_bounds else None,
        "crs_south_up": cfg.crs_south_up,
    }


def read_maps_manifest() -> dict:
    path = manifest_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_maps_manifest(
    *,
    map_ids: tuple[str, ...] | list[str] = KNOWN_MAP_IDS,
) -> dict:
    """Write ``data/maps/MANIFEST.json`` with generation date + layer signatures."""
    maps: dict[str, dict[str, object]] = {}
    for map_id in map_ids:
        mid = coerce_map_id(map_id)
        cfg = MAP_TILE_CONFIGS.get(mid) if mid is not None else None
        if cfg is None:
            continue
        entry = tile_config_signature(cfg)
        ref = map_dir(map_id) / _REFERENCE_PNG
        if ref.is_file():
            try:
                with Image.open(ref) as im:
                    entry["ref_width"] = im.size[0]
                    entry["ref_height"] = im.size[1]
            except OSError:
                pass
        maps[map_id] = entry

    payload = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "metaforge",
        "maps": maps,
    }
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def assets_outdated(map_id: str, *, manifest: dict | None = None) -> bool:
    """True when committed/cached basemap does not match current tile config."""
    mid = coerce_map_id(map_id)
    cfg = MAP_TILE_CONFIGS.get(mid) if mid is not None else None
    if cfg is None:
        return False
    ref = map_dir(map_id) / _REFERENCE_PNG
    if not ref.is_file():
        return True
    data = manifest if manifest is not None else read_maps_manifest()
    entry = (data.get("maps") or {}).get(map_id)
    if not isinstance(entry, dict):
        return True
    expected = tile_config_signature(cfg)
    return any(entry.get(key) != value for key, value in expected.items())


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def fetch_map_markers(
    map_id: str, *, session: requests.Session | None = None
) -> list[dict]:
    """GET game-map-data for one mapID."""
    sess = session or _session()
    resp = sess.get(
        METAFORGE_MAP_DATA_URL,
        params={"tableID": "arc_map_data", "mapID": map_id},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    markers = data.get("allData") or data.get("markers") or []
    if not isinstance(markers, list):
        return []
    return markers


def _join_list_or_str(value: object, sep: str = ", ") -> str:
    """Coerce a list or scalar field to a stripped string."""
    if isinstance(value, list):
        return sep.join(str(x).strip() for x in value if str(x).strip())
    return str(value or "").strip()


def _parse_marker_latlng(
    raw: dict,
) -> tuple[float, float] | None:
    raw_lat, raw_lng = raw.get("lat"), raw.get("lng")
    if raw_lat is None or raw_lng is None:
        return None
    try:
        return float(raw_lat), float(raw_lng)
    except (TypeError, ValueError):
        return None


def _parse_instance_name(raw: dict) -> str:
    inst = raw.get("instanceName")
    if isinstance(inst, list):
        return ", ".join(str(x) for x in inst if x)
    s = str(inst or "").strip()
    return "" if s.lower() == "none" else s


def _normalize_marker(raw: dict, map_id: str) -> dict | None:
    mid = str(raw.get("id") or "").strip()
    if not mid:
        return None
    coords = _parse_marker_latlng(raw)
    if coords is None:
        return None
    lat, lng = coords
    return {
        "id": mid,
        "map_id": map_id,
        "lat": lat,
        "lng": lng,
        "category": str(raw.get("category") or "").strip().lower(),
        "subcategory": str(raw.get("subcategory") or "").strip(),
        "instance_name": _parse_instance_name(raw),
        "loot_areas": _join_list_or_str(raw.get("lootAreas")),
        "behind_locked_door": 1 if raw.get("behindLockedDoor") else 0,
        "zlayers": int(raw.get("zlayers") or 0),
    }


def _download(sess: requests.Session, url: str, dest: Path) -> bool:
    try:
        resp = sess.get(url, timeout=60)
        if resp.status_code != _HTTP_OK or not resp.content:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
    except requests.RequestException:
        return False
    else:
        return True


def _tile_url(cfg: MapTileConfig, zoom: int, x: int, y: int, name_fmt: str) -> str:
    name = name_fmt.format(x=x, y=y)
    return f"{STATIC_BASE}/maps/{cfg.layer_path}/{zoom}/{name}"


def _tile_exists(
    sess: requests.Session, cfg: MapTileConfig, zoom: int, x: int, y: int, name_fmt: str
) -> bool:
    try:
        r = sess.head(
            _tile_url(cfg, zoom, x, y, name_fmt),
            timeout=15,
            allow_redirects=True,
        )
    except requests.RequestException:
        return False
    return r.status_code == _HTTP_OK


def _detect_tile_name_fmt(
    sess: requests.Session, cfg: MapTileConfig, zoom: int
) -> str | None:
    """Prefer ``z/x/y.webp``; fall back to ``z/x_y.webp`` if nested dirs 404."""
    # Probe a few likely coords so sparse maps (Pendola) still resolve.
    probes = (
        (0, 0),
        (1, 1),
        (2, 2),
        (3, 3),
        (4, 4),
        (5, 3),
        (5, 5),
        (8, 8),
    )
    for name_fmt in TILE_NAME_FMTS:
        for x, y in probes:
            if _tile_exists(sess, cfg, zoom, x, y, name_fmt):
                return name_fmt
        # Dense maps often only publish a short axis from origin — try x along y=0.
        for x in range(min(cfg.max_scan, 8)):
            if _tile_exists(sess, cfg, zoom, x, 0, name_fmt):
                return name_fmt
    return None


def _discover_tile_grid(
    sess: requests.Session,
    cfg: MapTileConfig,
    zoom: int,
    name_fmt: str,
) -> tuple[int, int, int, int] | None:
    """Return inclusive (min_x, max_x, min_y, max_y) for tiles that exist."""
    max_n = cfg.max_scan
    present: list[tuple[int, int]] = []
    # Fast path: dense grid starting at 0 (most maps).
    max_x = -1
    for x in range(max_n):
        if not _tile_exists(sess, cfg, zoom, x, 0, name_fmt):
            break
        max_x = x
    max_y = -1
    if max_x >= 0:
        for y in range(max_n):
            if not _tile_exists(sess, cfg, zoom, 0, y, name_fmt):
                break
            max_y = y
        if max_y >= 0:
            return 0, max_x, 0, max_y

    # Sparse path (e.g. Pendola): scan the zoom square.
    for x in range(max_n):
        present.extend(
            (x, y)
            for y in range(max_n)
            if _tile_exists(sess, cfg, zoom, x, y, name_fmt)
        )
    if not present:
        return None
    xs = [p[0] for p in present]
    ys = [p[1] for p in present]
    return min(xs), max(xs), min(ys), max(ys)


def _probe_actual_tile_size(
    sess: requests.Session,
    cfg: MapTileConfig,
    zoom: int,
    name_fmt: str,
    tile_dir: Path,
    *,
    min_x: int,
    max_x: int,
    min_y: int,
    max_y: int,
) -> int:
    """Read pixel width of the first downloadable tile (config may differ)."""
    for x0 in range(min_x, max_x + 1):
        for y0 in range(min_y, max_y + 1):
            probe = tile_dir / f"{x0}_{y0}.webp"
            url = _tile_url(cfg, zoom, x0, y0, name_fmt)
            if not probe.is_file() and not _download(sess, url, probe):
                continue
            try:
                with Image.open(probe) as tip:
                    return tip.size[0]
            except OSError:
                continue
        continue
        break
    return cfg.tile_size


def _stitch_tiles_onto_canvas(
    sess: requests.Session,
    cfg: MapTileConfig,
    zoom: int,
    name_fmt: str,
    tile_dir: Path,
    canvas: Image.Image,
    tile_size: int,
    xy_range: tuple[int, int, int, int],
) -> None:
    """Download tiles and paste them onto ``canvas`` in-place."""
    min_x, max_x, min_y, max_y = xy_range
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            dest = tile_dir / f"{x}_{y}.webp"
            url = _tile_url(cfg, zoom, x, y, name_fmt)
            if not dest.is_file() and not _download(sess, url, dest):
                continue
            try:
                with Image.open(dest) as tile:
                    canvas.paste(tile.convert("RGBA"), (x * tile_size, y * tile_size))
            except OSError:
                continue
            time.sleep(0.02)


def _stitch_reference(
    sess: requests.Session,
    map_id: str,
    out_path: Path,
    *,
    cfg: MapTileConfig | None = None,
) -> tuple[tuple[int, int], tuple[float, float, float, float]] | None:
    """Download and stitch Metaforge zoom tiles into a single PNG.

    Returns ``((width, height), (lat_min, lat_max, lng_min, lng_max))`` for the
    stitched image's CRS coverage.
    """
    if cfg is None:
        mid = coerce_map_id(map_id)
        cfg = MAP_TILE_CONFIGS.get(mid) if mid is not None else None
    if cfg is None:
        return None

    zoom = cfg.zoom
    name_fmt = _detect_tile_name_fmt(sess, cfg, zoom)
    if name_fmt is None:
        return None
    print(f"  Tile path format for {map_id}: …/{zoom}/{name_fmt}")
    grid = _discover_tile_grid(sess, cfg, zoom, name_fmt)
    if grid is None:
        return None
    min_x, max_x, min_y, max_y = grid
    # Always cover the full Metaforge worldExtent tile square so pin
    # projection matches Leaflet (subset grids used to shift CRS and park
    # markers in the black void around the PNG).
    n = max(1, 2**zoom)
    tile_dir = map_dir(map_id) / f"tiles_z{zoom}"
    tile_dir.mkdir(parents=True, exist_ok=True)

    # Prefer the real pixel size of the first tile — config can drift
    # (Dam's Metaforge layer ships 512px retina tiles with logical tileSize 256).
    tile_size = _probe_actual_tile_size(
        sess,
        cfg,
        zoom,
        name_fmt,
        tile_dir,
        min_x=min_x,
        max_x=max_x,
        min_y=min_y,
        max_y=max_y,
    )
    if tile_size != cfg.tile_size:
        print(
            f"  Note: {map_id} tiles are {tile_size}px "
            f"(config had {cfg.tile_size}px) — using actual size"
        )

    canvas = Image.new("RGBA", (n * tile_size, n * tile_size), (0, 0, 0, 255))
    _stitch_tiles_onto_canvas(
        sess,
        cfg,
        zoom,
        name_fmt,
        tile_dir,
        canvas,
        tile_size,
        (min_x, max_x, min_y, max_y),
    )

    # Full 2^z grid in zoom-0 CRS → world via tileExtent affine (not a linear
    # stretch of worldExtent onto the square). Crop empty margins and remap.
    bounds = tile_grid_crs_bounds(cfg, 0, n - 1, 0, n - 1)
    canvas, bounds = crop_reference_to_content(
        canvas, bounds, south_up=cfg.crs_south_up
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG")
    write_reference_bounds(map_id, bounds)
    print(
        f"  Stitched {map_id}: {canvas.size[0]}x{canvas.size[1]} "
        f"from tiles x={min_x}..{max_x} y={min_y}..{max_y} on {n}x{n} world grid "
        f"(CRS lat[{bounds[0]:.1f},{bounds[1]:.1f}] lng[{bounds[2]:.1f},{bounds[3]:.1f}])"
    )
    return canvas.size, bounds


def _download_static_custom_png(
    sess: requests.Session, map_id: str, out_path: Path
) -> tuple[int, int] | None:
    """Fetch Metaforge's full-map PNG from static hosting (no tile pyramid)."""
    tmp = out_path.with_suffix(".download.png")
    url = f"{STATIC_BASE}/custom/{map_id}.png"
    if not _download(sess, url, tmp):
        return None
    try:
        with Image.open(tmp) as raw:
            rgba = raw.convert("RGBA")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            rgba.save(out_path, format="PNG")
            size = rgba.size
    except OSError:
        tmp.unlink(missing_ok=True)
        return None
    tmp.unlink(missing_ok=True)
    return size


def _download_overview(
    sess: requests.Session,
    map_id: str,
    overview: Path,
    mid: MapId | None,
) -> None:
    """Try multiple CDN locations for the overview thumbnail."""
    slug = cdn_slug(map_id)
    ok = _download(sess, f"{CDN_BASE}/ui/{slug}.webp", overview)
    if not ok and slug != map_id:
        ok = _download(sess, f"{CDN_BASE}/ui/{map_id}.webp", overview)
    if not ok and mid is not None:
        tile = _STATIC_UI_TILES.get(mid)
        if tile:
            _download(sess, f"{STATIC_BASE}/ui/{tile}", overview)


def _download_reference(
    sess: requests.Session,
    map_id: str,
    reference: Path,
    cfg: MapTileConfig | None,
    *,
    force: bool,
) -> tuple[tuple[int, int] | None, tuple[float, float, float, float] | None]:
    """Download (or stitch) the basemap reference PNG; return (size, crs_bounds)."""
    if force and reference.is_file():
        reference.unlink()
    print(f"  Downloading basemap tiles for {map_id}...")
    stitched = _stitch_reference(sess, map_id, reference, cfg=cfg)
    if stitched is not None:
        return stitched
    print(f"  CDN tiles missing — trying static custom PNG for {map_id}...")
    size = _download_static_custom_png(sess, map_id, reference)
    if size is None:
        return None, None
    crs_bounds: tuple[float, float, float, float] | None = None
    if cfg is not None:
        crs_bounds = world_extent_bounds(cfg.world_extent)
        write_reference_bounds(map_id, crs_bounds)
    return size, crs_bounds


def _load_cached_reference(
    map_id: str,
    reference: Path,
    cfg: MapTileConfig | None,
) -> tuple[tuple[int, int] | None, tuple[float, float, float, float] | None]:
    """Read cached reference PNG size and bounds from disk."""
    with Image.open(reference) as im:
        size: tuple[int, int] | None = im.size
    crs_bounds = read_reference_bounds(map_id)
    if crs_bounds is None and cfg is not None:
        crs_bounds = infer_reference_bounds_from_tiles(map_id, cfg)
        if crs_bounds is not None:
            write_reference_bounds(map_id, crs_bounds)
    return size, crs_bounds


def download_map_assets(
    map_id: str,
    *,
    session: requests.Session | None = None,
    force: bool = False,
) -> dict[str, str | int | float]:
    """Cache overview + reference basemap under data/maps/{map_id}/."""
    sess = session or _session()
    mdir = map_dir(map_id)
    mdir.mkdir(parents=True, exist_ok=True)
    mid = coerce_map_id(map_id)
    cfg = MAP_TILE_CONFIGS.get(mid) if mid is not None else None
    overview = mdir / "overview.webp"
    if force or not overview.is_file():
        # Prefer dated-layer overview from CDN ui slug, then static picker tile.
        _download_overview(sess, map_id, overview, mid)

    reference = mdir / _REFERENCE_PNG
    size: tuple[int, int] | None = None
    crs_bounds: tuple[float, float, float, float] | None = None
    if force or not reference.is_file():
        size, crs_bounds = _download_reference(
            sess, map_id, reference, cfg, force=force
        )
    elif reference.is_file():
        size, crs_bounds = _load_cached_reference(map_id, reference, cfg)

    # Never promote the tiny picker overview as the CV/F8 reference.
    if size is None:
        return {
            "overview_rel": f"maps/{map_id}/overview.webp"
            if overview.is_file()
            else "",
            "reference_rel": "",
            "ref_width": 0,
            "ref_height": 0,
        }

    rel_overview = f"maps/{map_id}/overview.webp" if overview.is_file() else ""
    rel_ref = f"maps/{map_id}/{_REFERENCE_PNG}" if reference.is_file() else ""

    out: dict[str, str | int | float] = {
        "overview_rel": rel_overview,
        "reference_rel": rel_ref,
        "ref_width": int(size[0]),
        "ref_height": int(size[1]),
    }
    if crs_bounds is not None:
        out["lat_min"] = float(crs_bounds[0])
        out["lat_max"] = float(crs_bounds[1])
        out["lng_min"] = float(crs_bounds[2])
        out["lng_max"] = float(crs_bounds[3])
    return out


def fetch_item_loot_areas(
    *,
    session: requests.Session | None = None,
) -> dict[str, str]:
    """Paginate Metaforge items and collect name → non-empty loot_area string."""
    return {
        name: meta["loot_areas"]
        for name, meta in fetch_item_index(session=session).items()
        if meta.get("loot_areas")
    }


def fetch_item_loot_index(
    *,
    session: requests.Session | None = None,
) -> dict[str, dict[str, str]]:
    """Alias for :func:`fetch_item_index` (includes items with empty loot tags)."""
    return fetch_item_index(session=session)


def _parse_item_loot_area(la: object) -> str:
    """Coerce Metaforge ``loot_area`` (list or str) to a comma-separated string."""
    if isinstance(la, list):
        return ", ".join(str(x).strip() for x in la if str(x).strip())
    return str(la).strip() if la else ""


def _index_item(it: dict, out: dict[str, dict[str, str]]) -> None:
    """Add one Metaforge item dict to the running index (mutates ``out``)."""
    name = (it.get("name") or "").strip()
    if not name:
        return
    item_id = str(it.get("id") or "").strip() or _slugify_item(name)
    out[name] = {
        "item_id": item_id,
        "loot_areas": _parse_item_loot_area(it.get("loot_area")),
    }


def fetch_item_index(
    *,
    session: requests.Session | None = None,
) -> dict[str, dict[str, str]]:
    """Paginate Metaforge items → ``{name: {item_id, loot_areas}}``.

    Includes items with **empty** ``loot_area`` (e.g. Insulation Roll). Those
    still have precise map spawn dots on Metaforge's item-view feed.
    """
    sess = session or _session()
    out: dict[str, dict[str, str]] = {}
    page = 1
    while True:
        resp = sess.get(
            f"{METAFORGE_API_BASE}/items",
            params={"page": page, "limit": 100},
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        for it in payload.get("data") or []:
            _index_item(it, out)
        pagination = payload.get("pagination") or {}
        if not pagination.get("hasNextPage", False):
            break
        page += 1
        time.sleep(0.05)
    return out


def _resolve_search_pool(items: list[dict], q_l: str) -> list[dict] | None:
    """Return the winning candidate pool, or None when ambiguous / empty."""
    exact = [it for it in items if (it.get("name") or "").strip().lower() == q_l]
    if exact:
        return exact
    prefix = [
        it for it in items if (it.get("name") or "").strip().lower().startswith(q_l)
    ]
    return prefix if len(prefix) == 1 else None


def lookup_item_by_search(
    query: str,
    *,
    session: requests.Session | None = None,
) -> tuple[str, str] | None:
    """Resolve a display name via Metaforge ``/items?search=``.

    Returns ``(item_id, canonical_name)`` for an exact (case-insensitive) hit,
    else the first result when the query uniquely prefixes one name.
    """
    q = (query or "").strip()
    if len(q) < _MIN_PAIR:
        return None
    sess = session or _session()
    resp = sess.get(
        f"{METAFORGE_API_BASE}/items",
        params={"search": q, "limit": 20},
        timeout=30,
    )
    resp.raise_for_status()
    items = resp.json().get("data") or []
    if not items:
        return None
    pool = _resolve_search_pool(items, q.lower())
    if not pool:
        return None
    it = pool[0]
    name = (it.get("name") or "").strip()
    item_id = str(it.get("id") or "").strip() or _slugify_item(name)
    if not name or not item_id:
        return None
    return item_id, name


def _slugify_item(name: str) -> str:
    s = name.strip().lower().replace("'", "").replace(".", "")
    out = []
    prev_dash = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def _pool_resolve_numeric_list(
    v: list, pool: list, go: Callable[[object], object]
) -> list:
    """Decide whether a numeric-only list holds raw values or pool refs."""
    if any(isinstance(x, float) for x in v):
        return list(v)
    if len(v) >= _MIN_POOL_REF_LIST_LEN and max(int(x) for x in v) >= len(pool):
        return list(v)
    if all(isinstance(x, int) and 0 <= x < len(pool) for x in v):
        return [go(x) for x in v]
    return list(v)


def _pool_resolve_list(v: list, pool: list, go: Callable[[object], object]) -> list:
    """Resolve a pool list entry, handling numeric-ref vs mixed lists."""
    all_numeric = v and all(
        isinstance(x, (int, float)) and not isinstance(x, bool) for x in v
    )
    if all_numeric:
        return _pool_resolve_numeric_list(v, pool, go)
    return [go(x) if isinstance(x, int) else x for x in v]


def _pool_go(i: object, pool: list, memo: dict) -> object:
    """Recursively resolve one pool index to its concrete value."""
    if not isinstance(i, int):
        return i
    if i in memo:
        return memo[i]
    if i < 0 or i >= len(pool):
        return i
    v = pool[i]
    memo[i] = None  # cycle guard
    if v is None or isinstance(v, (str, bool, float, int)):
        memo[i] = v
    elif isinstance(v, dict):
        memo[i] = {k: _pool_go(val, pool, memo) for k, val in v.items()}
    elif isinstance(v, list):
        memo[i] = _pool_resolve_list(v, pool, lambda x: _pool_go(x, pool, memo))
    else:
        memo[i] = v
    return memo[i]


def _decode_sveltekit_pool(pool: list) -> object:
    """Resolve SvelteKit ``__data.json`` devalue-style integer refs."""
    memo: dict[int, object] = {}
    return _pool_go(0, pool, memo)


def cluster_loose_spots(
    spots: list[tuple[float, float]],
    *,
    radius: float = LOOSE_CLUSTER_RADIUS,
) -> list[tuple[float, float, int]]:
    """Greedy cluster of (lat, lng) points → (lat, lng, count)."""
    if not spots:
        return []
    r2 = radius * radius
    remaining = list(spots)
    clusters: list[tuple[float, float, int]] = []
    while remaining:
        seed_lat, seed_lng = remaining.pop(0)
        members = [(seed_lat, seed_lng)]
        kept: list[tuple[float, float]] = []
        for lat, lng in remaining:
            dlat = lat - seed_lat
            dlng = lng - seed_lng
            if dlat * dlat + dlng * dlng <= r2:
                members.append((lat, lng))
            else:
                kept.append((lat, lng))
        remaining = kept
        clat = sum(m[0] for m in members) / len(members)
        clng = sum(m[1] for m in members) / len(members)
        clusters.append((clat, clng, len(members)))
    clusters.sort(key=lambda c: (-c[2], c[0], c[1]))
    return clusters


def _extract_loose_spots_from_root(root: dict) -> list | None:
    """Navigate the decoded pool root to the raw spots list (or None)."""
    item_view = root.get("itemView")
    if not isinstance(item_view, dict):
        return None
    loose = item_view.get("loose")
    if not isinstance(loose, dict):
        return None
    raw = loose.get("spots") or []
    return raw if isinstance(raw, list) else None


def _parse_spot_rows(raw_spots: list) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for row in raw_spots:
        if not isinstance(row, (list, tuple)) or len(row) < _MIN_PAIR:
            continue
        try:
            out.append((float(row[0]), float(row[1])))
        except (TypeError, ValueError):
            continue
    return out


def fetch_item_loose_spots(
    map_id: str,
    item_id: str,
    *,
    session: requests.Session | None = None,
) -> list[tuple[float, float]]:
    """Fetch Metaforge item-view loose spawn coords for one map + item slug.

    Uses the public SvelteKit ``__data.json`` payload (same data as the map UI
    Dots/Heatmap layer). Returns an empty list when the item has no precise
    spots on that map.
    """
    sess = session or _session()
    slug = (item_id or "").strip()
    if not slug:
        return []
    resp = sess.get(
        f"{METAFORGE_SITE}/arc-raiders/map/{map_id}/__data.json",
        params={"item": slug},
        timeout=60,
    )
    resp.raise_for_status()
    payload = resp.json()
    nodes = payload.get("nodes") or []
    if len(nodes) < _MIN_PAIR:
        return []
    pool = nodes[1].get("data")
    if not isinstance(pool, list) or not pool:
        return []
    root = _decode_sveltekit_pool(pool)
    if not isinstance(root, dict):
        return []
    raw_spots = _extract_loose_spots_from_root(root)
    if raw_spots is None:
        return []
    return _parse_spot_rows(raw_spots)


def _fallback_bounds_from_rows(
    rows: list[dict],
) -> tuple[float, float, float, float]:
    """Derive padded lat/lng bounds from marker rows when no tile config exists."""
    lats = [r["lat"] for r in rows]
    lngs = [r["lng"] for r in rows]
    lat_min, lat_max = min(lats), max(lats)
    lng_min, lng_max = min(lngs), max(lngs)
    pad_lat = (lat_max - lat_min) * 0.02 or 1.0
    pad_lng = (lng_max - lng_min) * 0.02 or 1.0
    return lat_min - pad_lat, lat_max + pad_lat, lng_min - pad_lng, lng_max + pad_lng


def _initial_bounds_for_map(
    _map_id: str,
    cfg: MapTileConfig | None,
    rows: list[dict],
) -> tuple[float, float, float, float]:
    """Best-effort initial CRS bounds before asset download."""
    if cfg is not None:
        return world_extent_bounds(cfg.world_extent)
    if rows:
        return _fallback_bounds_from_rows(rows)
    return 0.0, 1.0, 0.0, 1.0


def _fetch_and_store_markers(
    conn: sqlite3.Connection,
    map_id: str,
    sess: requests.Session,
) -> list[dict] | None:
    """Fetch markers for one map; return normalised rows or None on error."""

    print(f"Fetching map markers: {map_id}...")
    try:
        raw = fetch_map_markers(map_id, session=sess)
    except Exception as e:  # ruff: ignore[blind-except]
        print(f"  Failed {map_id}: {e}")
        return None

    rows = [norm for r in raw if (norm := _normalize_marker(r, map_id))]
    conn.execute("DELETE FROM map_markers WHERE map_id = ?", (map_id,))
    conn.executemany(
        """
        INSERT INTO map_markers (
            id, map_id, lat, lng, category, subcategory,
            instance_name, loot_areas, behind_locked_door, zlayers
        ) VALUES (
            :id, :map_id, :lat, :lng, :category, :subcategory,
            :instance_name, :loot_areas, :behind_locked_door, :zlayers
        )
        """,
        rows,
    )
    return rows


def _update_asset_info(
    map_id: str,
    sess: requests.Session,
    manifest: dict,
    *,
    force_assets: bool,
) -> tuple[dict[str, str | int | float], bool]:
    """Download (or validate cache of) basemap assets; return (info, changed)."""
    refresh = force_assets or assets_outdated(map_id, manifest=manifest)
    if refresh:
        reason = "forced" if force_assets else "manifest outdated / missing"
        print(f"  Basemap refresh for {map_id} ({reason})")
    else:
        print(f"  Using cached basemap for {map_id}")
    before = map_dir(map_id) / _REFERENCE_PNG
    before_mtime = before.stat().st_mtime if before.is_file() else None
    try:
        asset_info = download_map_assets(map_id, session=sess, force=refresh)
    except Exception as e:  # ruff: ignore[blind-except]
        print(f"  Asset download failed for {map_id}: {e}")
        return {
            "overview_rel": "",
            "reference_rel": "",
            "ref_width": 0,
            "ref_height": 0,
        }, False
    after = map_dir(map_id) / _REFERENCE_PNG
    after_mtime = after.stat().st_mtime if after.is_file() else None
    changed = after_mtime is not None and after_mtime != before_mtime
    return asset_info, changed


def _refine_bounds_from_assets(
    map_id: str,
    cfg: MapTileConfig | None,
    asset_info: dict,
    lat_min: float,
    lat_max: float,
    lng_min: float,
    lng_max: float,
) -> tuple[float, float, float, float]:
    """Prefer CRS from actual stitched reference when known."""
    if all(k in asset_info for k in ("lat_min", "lat_max", "lng_min", "lng_max")):
        return (
            float(asset_info["lat_min"]),
            float(asset_info["lat_max"]),
            float(asset_info["lng_min"]),
            float(asset_info["lng_max"]),
        )
    if cfg is not None:
        inferred = infer_reference_bounds_from_tiles(map_id, cfg)
        if inferred is not None:
            write_reference_bounds(map_id, inferred)
            return inferred
    return lat_min, lat_max, lng_min, lng_max


def _upsert_map_meta(
    conn: sqlite3.Connection,
    map_id: str,
    lat_min: float,
    lat_max: float,
    lng_min: float,
    lng_max: float,
    row_count: int,
    asset_info: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO map_meta (
            map_id, display_name, lat_min, lat_max, lng_min, lng_max,
            marker_count, overview_rel, reference_rel, ref_width, ref_height
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(map_id) DO UPDATE SET
            display_name=excluded.display_name,
            lat_min=excluded.lat_min,
            lat_max=excluded.lat_max,
            lng_min=excluded.lng_min,
            lng_max=excluded.lng_max,
            marker_count=excluded.marker_count,
            overview_rel=excluded.overview_rel,
            reference_rel=excluded.reference_rel,
            ref_width=excluded.ref_width,
            ref_height=excluded.ref_height
        """,
        (
            map_id,
            map_display_name(map_id),
            lat_min,
            lat_max,
            lng_min,
            lng_max,
            row_count,
            asset_info.get("overview_rel") or "",
            asset_info.get("reference_rel") or "",
            int(asset_info.get("ref_width") or 0),
            int(asset_info.get("ref_height") or 0),
        ),
    )


def _fetch_and_store_loot_index(
    conn: sqlite3.Connection,
    sess: requests.Session,
    stats: dict,
) -> dict:
    """Fetch Metaforge item index and store in DB; return index (empty on error)."""
    print("Fetching Metaforge item index (all items, incl. no loot tags)...")
    try:
        loot_index = fetch_item_index(session=sess)
        conn.execute("DELETE FROM item_loot_areas")
        conn.executemany(
            "INSERT INTO item_loot_areas (name, loot_areas, item_id) VALUES (?, ?, ?)",
            [
                (name, meta["loot_areas"], meta["item_id"])
                for name, meta in loot_index.items()
            ],
        )
        conn.commit()
    except Exception as e:  # ruff: ignore[blind-except]
        print(f"  Item index fetch failed: {e}")
        stats["_loot_areas"] = 0
        return {}
    else:
        tagged = sum(1 for m in loot_index.values() if m.get("loot_areas"))
        print(f"  Stored {len(loot_index)} items ({tagged} with loot-area tags)")
        stats["_loot_areas"] = len(loot_index)
        return loot_index


def _fetch_and_store_loose_spots(
    conn: sqlite3.Connection,
    sess: requests.Session,
    loot_index: dict,
    map_ids: list[str],
    stats: dict,
) -> None:
    """Prefetch loose-spot clusters for all loot-tagged items."""
    bulk = {n: m for n, m in loot_index.items() if (m.get("loot_areas") or "").strip()}
    print(
        "Fetching Metaforge loose item spots "
        f"({len(bulk)} loot-tagged items × {len(map_ids)} maps)..."
    )
    try:
        n_clusters = _store_loose_spot_clusters(
            conn, loot_index=bulk, map_ids=list(map_ids), session=sess
        )
        stats["_loose_clusters"] = n_clusters
        print(f"  Stored {n_clusters} loose-spot clusters")
    except Exception as e:  # ruff: ignore[blind-except]
        print(f"  Loose-spot fetch failed: {e}")
        stats["_loose_clusters"] = 0


def _ingest_one_map(
    conn: sqlite3.Connection,
    map_id: str,
    sess: requests.Session,
    manifest: dict,
    stats: dict,
    *,
    download_assets: bool,
    force_assets: bool,
) -> bool:
    """Ingest one map's markers + assets; return True if reference changed."""

    rows = _fetch_and_store_markers(conn, map_id, sess)
    if rows is None:
        stats[map_id] = 0
        return False

    mid = coerce_map_id(map_id)
    cfg = MAP_TILE_CONFIGS.get(mid) if mid is not None else None
    lat_min, lat_max, lng_min, lng_max = _initial_bounds_for_map(map_id, cfg, rows)

    asset_info: dict[str, str | int | float] = {
        "overview_rel": "",
        "reference_rel": "",
        "ref_width": 0,
        "ref_height": 0,
    }
    asset_changed = False
    if download_assets and rows:
        asset_info, asset_changed = _update_asset_info(
            map_id, sess, manifest, force_assets=force_assets
        )

    lat_min, lat_max, lng_min, lng_max = _refine_bounds_from_assets(
        map_id, cfg, asset_info, lat_min, lat_max, lng_min, lng_max
    )
    _upsert_map_meta(
        conn, map_id, lat_min, lat_max, lng_min, lng_max, len(rows), asset_info
    )
    conn.commit()
    stats[map_id] = len(rows)
    print(f"  Stored {len(rows)} markers for {map_id}")
    time.sleep(0.1)
    return asset_changed


def store_map_catalog(
    db_path: Path,
    *,
    map_ids: tuple[str, ...] | list[str] = KNOWN_MAP_IDS,
    download_assets: bool = True,
    force_assets: bool = False,
    fetch_loot_areas: bool = True,
    fetch_loose_spots: bool = False,
) -> dict[str, int]:
    """Fetch markers (+ optional assets / item loot areas) into SQLite.

    ``fetch_loose_spots`` walks every loot-tagged item × map against Metaforge's
    item-view feed (~minutes). Prefer lazy cache via ``MapCatalog.ensure_loose_spots``
    unless running an explicit ``--loose-spots`` refresh.
    """
    import sqlite3

    sess = _session()
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    maps_dir().mkdir(parents=True, exist_ok=True)

    stats: dict[str, int] = {}
    manifest = read_maps_manifest()
    assets_changed = False
    conn = sqlite3.connect(db_path)
    try:
        ensure_map_schema(conn)
        for map_id in map_ids:
            changed = _ingest_one_map(
                conn,
                map_id,
                sess,
                manifest,
                stats,
                download_assets=download_assets,
                force_assets=force_assets,
            )
            if changed:
                assets_changed = True

        loot_index: dict = {}
        if fetch_loot_areas:
            loot_index = _fetch_and_store_loot_index(conn, sess, stats)

        if fetch_loose_spots and loot_index:
            _fetch_and_store_loose_spots(conn, sess, loot_index, list(map_ids), stats)
    finally:
        conn.close()

    # Bump MANIFEST date only when basemaps change (or first create) so weekly
    # marker-only CI runs do not dirty the git tree with a new timestamp.
    if download_assets and (
        force_assets or assets_changed or not manifest_path().is_file()
    ):
        written = write_maps_manifest(map_ids=map_ids)
        print(f"  Wrote maps manifest @ {written.get('generated_at')}")

    return stats


def _store_loose_spot_clusters(
    conn: object,
    *,
    loot_index: dict[str, dict[str, str]],
    map_ids: list[str],
    session: requests.Session,
) -> int:
    """Fetch + cluster loose spots for every loot-tagged item × map."""
    import sqlite3

    assert isinstance(conn, sqlite3.Connection)
    conn.execute("DELETE FROM map_item_spot_clusters")
    conn.execute("DELETE FROM map_item_spots_meta")
    total_clusters = 0
    fetched_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = sorted(loot_index.items(), key=lambda kv: kv[0].lower())
    for i, (name, meta) in enumerate(items, start=1):
        item_id = meta["item_id"]
        if i == 1 or i % 25 == 0 or i == len(items):
            print(f"  Loose spots progress {i}/{len(items)} ({name})...")
        for map_id in map_ids:
            try:
                spots = fetch_item_loose_spots(map_id, item_id, session=session)
            except Exception as e:  # ruff: ignore[blind-except]
                print(f"    skip {map_id}/{item_id}: {e}")
                continue
            clusters = cluster_loose_spots(spots)
            conn.execute(
                """
                INSERT OR REPLACE INTO map_item_spots_meta
                    (map_id, item_id, item_name, raw_count, cluster_count, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    map_id,
                    item_id,
                    name,
                    len(spots),
                    len(clusters),
                    fetched_at,
                ),
            )
            if clusters:
                conn.executemany(
                    """
                    INSERT INTO map_item_spot_clusters
                        (map_id, item_id, item_name, lat, lng, count)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (map_id, item_id, name, lat, lng, count)
                        for lat, lng, count in clusters
                    ],
                )
                total_clusters += len(clusters)
            time.sleep(0.03)
        if i % 10 == 0:
            conn.commit()
    conn.commit()
    return total_clusters


# Re-export for callers that need slug map
__all__ = [
    "KNOWN_MAP_IDS",
    "MAP_TILE_CONFIGS",
    "assets_outdated",
    "cluster_loose_spots",
    "download_map_assets",
    "extent_affine",
    "fetch_item_index",
    "fetch_item_loose_spots",
    "fetch_item_loot_areas",
    "fetch_item_loot_index",
    "fetch_map_markers",
    "infer_reference_bounds_from_tiles",
    "latlng_to_tile_crs",
    "lookup_item_by_search",
    "read_maps_manifest",
    "read_reference_bounds",
    "store_map_catalog",
    "tile_config_signature",
    "tile_crs_to_latlng",
    "tile_grid_crs_bounds",
    "world_extent_bounds",
    "write_maps_manifest",
    "write_reference_bounds",
]
