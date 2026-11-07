"""In-game map CV: detect MAP UI, register viewport to basemap, optional player."""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .config import APP_DIR
from .config import get_settings
from .map_data import MAP_DISPLAY_NAMES
from .map_data import MapCatalog
from .map_data import MapId
from .map_data import MapMeta
from .map_data import get_map_catalog
from .map_data import latlng_to_norm
from .map_data import norm_to_latlng
from .map_data import reference_path
from .ocr import get_ocr_engine
from .ocr import grab_screen

logger = logging.getLogger("arc_helper")

# Teal / cyan player marker in HSV (OpenCV H: 0-179)
_TEAL_H_LOW = 75
_TEAL_H_HIGH = 100
_TEAL_S_LOW = 80
_TEAL_V_LOW = 80

_MIN_MARKER_AREA = 20
_MAX_MARKER_ASPECT = 4.0
_EPS = 1e-6
_MIN_ORB_MATCHES = 8
_MIN_KNN_PAIR = 2
_MIN_SCALE_POINTS = 2
_MIN_RANSAC_SCALE = 0.05
_MAX_RANSAC_SCALE = 40.0
_MIN_INLIER_RATIO = 0.15
_VIEWPORT_MARGIN_PX = 20
_NORM_LOOSE_MIN = -0.2
_NORM_LOOSE_MAX = 1.2
_THUMB_DIFF_PIXEL = 18
_THUMB_CHANGED_FRAC = 0.12
_NORM_TIGHT_MIN = -0.05
_NORM_TIGHT_MAX = 1.05

# OCR / in-game title text → MapId (aliases folded onto MapId consts).
_MAP_TITLE_TO_ID: dict[str, MapId] = {
    "the blue gate": MapId.BLUE_GATE,
    "blue gate": MapId.BLUE_GATE,
    "dam battlegrounds": MapId.DAM,
    "the dam": MapId.DAM,
    "dam": MapId.DAM,
    "spaceport": MapId.SPACEPORT,
    "the spaceport": MapId.SPACEPORT,
    "buried city": MapId.BURIED_CITY,
    "stella montis": MapId.STELLA_MONTIS,
    "riven tides": MapId.RIVEN_TIDES,
    "pendola pass": MapId.PENDOLA_PASS,
}
# Also accept the canonical display labels (lowercased).
for _mid, _label in MAP_DISPLAY_NAMES.items():
    _MAP_TITLE_TO_ID.setdefault(_label.lower(), _mid)


@dataclass
class PlayerFix:
    """A single position estimate from the open in-game map."""

    map_id: str
    lat: float
    lng: float
    facing: float | None = None  # radians, 0=east, CCW
    confidence: float = 0.0
    timestamp: float = 0.0


@dataclass
class ViewportRegistration:
    """ORB match of the open map terrain to the Metaforge basemap."""

    map_id: str
    # Physical-pixel bbox of the cropped viewport on the full screen
    viewport_bbox: tuple[int, int, int, int]  # left, top, right, bottom
    # 2x3 affine: viewport px → reference px
    affine_vp_to_ref: np.ndarray
    # 2x3 affine: reference px → viewport px
    affine_ref_to_vp: np.ndarray
    confidence: float
    ref_width: int
    ref_height: int
    timestamp: float = 0.0
    player: PlayerFix | None = None


def player_arrow_template() -> Path:
    bundled = APP_DIR / "static" / "map_sync" / "player_arrow.png"
    if bundled.is_file():
        return bundled
    # Dev tree: repo static/
    return (
        Path(__file__).resolve().parents[2] / "static" / "map_sync" / "player_arrow.png"
    )


def find_teal_player(
    image: Image.Image,
) -> tuple[tuple[float, float], float | None, float] | None:
    """Locate teal player chevron.

    Returns ((x, y), facing_radians_or_None, score) in image pixel coords,
    or None if not found.
    """
    try:
        import cv2
    except ImportError:
        logger.warning("opencv not available; cannot find player marker")
        return None

    bgr = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array([_TEAL_H_LOW, _TEAL_S_LOW, _TEAL_V_LOW], dtype=np.uint8),
        np.array([_TEAL_H_HIGH, 255, 255], dtype=np.uint8),
    )
    # Clean small noise
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    # Prefer mid-sized blobs (arrow), ignore tiny noise / huge panels
    h, w = mask.shape[:2]
    img_area = float(h * w)
    best = None
    best_score = 0.0
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area < _MIN_MARKER_AREA or area > img_area * 0.05:
            continue
        _x, _y, bw, bh = cv2.boundingRect(cnt)
        # Prefer compact-ish shapes
        aspect = max(bw, bh) / max(min(bw, bh), 1)
        if aspect > _MAX_MARKER_ASPECT:
            continue
        moments = cv2.moments(cnt)
        if moments["m00"] <= _EPS:
            continue
        cx = float(moments["m10"] / moments["m00"])
        cy = float(moments["m01"] / moments["m00"])
        # Score: area with mild preference for center of frame
        dist_pen = math.hypot(cx - w / 2, cy - h / 2) / (math.hypot(w, h) / 2)
        score = area * (1.2 - 0.4 * dist_pen)
        if score > best_score:
            best_score = score
            # Facing from PCA / arrow tip = farthest contour point from centroid
            pts = cnt.reshape(-1, 2).astype(np.float64)
            d = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
            tip = pts[int(np.argmax(d))]
            facing = math.atan2(-(tip[1] - cy), tip[0] - cx)  # screen y down → flip
            best = ((cx, cy), facing, score)

    return best


def _fit_positive_scale_translation(
    src: np.ndarray,
    dst: np.ndarray,
) -> tuple[float, float, float] | None:
    """Least squares ``dst ≈ s·src + t`` with ``s > 0`` (no rotation)."""
    if src.shape[0] < _MIN_SCALE_POINTS:
        return None
    p_mean = src.mean(axis=0)
    q_mean = dst.mean(axis=0)
    pc = src - p_mean
    qc = dst - q_mean
    denom = float(np.dot(pc.ravel(), pc.ravel()))
    if denom < _EPS:
        return None
    scale = float(np.dot(pc.ravel(), qc.ravel()) / denom)
    if scale <= _EPS:
        return None
    translation = q_mean - scale * p_mean
    return scale, float(translation[0]), float(translation[1])


def estimate_upright_scale_translation(
    src: np.ndarray,
    dst: np.ndarray,
    *,
    reproj_thresh: float = 5.0,
    max_iters: int = 80,
    min_inliers: int = 6,
) -> tuple[np.ndarray, float] | None:
    """RANSAC scale+translation. The raid map does not rotate or flip.

    Returns ``(2x3 affine, inlier_ratio)`` mapping ``src`` → ``dst``, or None.
    A half-turn correspondence has negative scale and is not a candidate.
    """
    src_pts = np.asarray(src, dtype=np.float64).reshape(-1, 2)
    dst_pts = np.asarray(dst, dtype=np.float64).reshape(-1, 2)
    count = src_pts.shape[0]
    if count < _MIN_SCALE_POINTS or dst_pts.shape[0] != count:
        return None

    rng = np.random.default_rng()
    best_mask: np.ndarray | None = None
    best_count = 0
    early = int(count * 0.85)
    for _ in range(max_iters):
        i, j = rng.choice(count, size=_MIN_SCALE_POINTS, replace=False)
        sample = _fit_positive_scale_translation(src_pts[[i, j]], dst_pts[[i, j]])
        if sample is None:
            continue
        scale, tx, ty = sample
        if scale < _MIN_RANSAC_SCALE or scale > _MAX_RANSAC_SCALE:
            continue
        err = np.hypot(
            scale * src_pts[:, 0] + tx - dst_pts[:, 0],
            scale * src_pts[:, 1] + ty - dst_pts[:, 1],
        )
        mask = err <= reproj_thresh
        hits = int(mask.sum())
        if hits > best_count:
            best_count = hits
            best_mask = mask
            if hits >= early:
                break
    if best_mask is None or best_count < min_inliers:
        return None

    fitted = _fit_positive_scale_translation(src_pts[best_mask], dst_pts[best_mask])
    if fitted is None:
        return None
    scale, tx, ty = fitted
    err = np.hypot(
        scale * src_pts[:, 0] + tx - dst_pts[:, 0],
        scale * src_pts[:, 1] + ty - dst_pts[:, 1],
    )
    inliers = int((err <= reproj_thresh).sum())
    if inliers < min_inliers:
        return None
    affine = np.array([[scale, 0.0, tx], [0.0, scale, ty]], dtype=np.float64)
    return affine, inliers / count


def _orb_upright(detector: object, gray: np.ndarray) -> tuple[Any, Any]:
    """ORB keypoints with orientation fixed at 0°.

    Default ORB rotates each patch to the keypoint angle, so a 180° patch can
    still match. The raid map has one orientation; upright descriptors skip
    that search and reject flipped terrain.
    """
    detect = getattr(detector, "detect", None)
    compute = getattr(detector, "compute", None)
    if detect is None or compute is None:
        return [], None
    keypoints = detect(gray, None)
    if not keypoints:
        return [], None
    for kp in keypoints:
        kp.angle = 0.0
    return compute(gray, keypoints)


def register_viewport_to_reference(
    viewport: Image.Image,
    reference: Image.Image,
) -> tuple[np.ndarray, float] | None:
    """Estimate upright scale+translation mapping viewport px → reference px.

    The raid map does not rotate. Returns (2x3 affine matrix, inlier_ratio)
    or None.
    """
    try:
        import cv2
    except ImportError:
        return None

    ref = cv2.cvtColor(np.array(reference.convert("RGB")), cv2.COLOR_RGB2GRAY)
    vp = cv2.cvtColor(np.array(viewport.convert("RGB")), cv2.COLOR_RGB2GRAY)

    # Downscale huge references for feature speed
    max_dim = 1600
    scale_ref = 1.0
    rh, rw = ref.shape[:2]
    if max(rh, rw) > max_dim:
        scale_ref = max_dim / max(rh, rw)
        ref = cv2.resize(
            ref,
            (int(rw * scale_ref), int(rh * scale_ref)),
            interpolation=cv2.INTER_AREA,
        )

    # ORB_create is a cv2 factory; stubs often omit it.
    orb_create = getattr(cv2, "ORB_create", None)
    if orb_create is None:
        return None
    detector = orb_create(4000)
    kp1, des1 = _orb_upright(detector, vp)
    kp2, des2 = _orb_upright(detector, ref)
    if (
        des1 is None
        or des2 is None
        or len(kp1) < _MIN_ORB_MATCHES
        or len(kp2) < _MIN_ORB_MATCHES
    ):
        return None

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    knn = matcher.knnMatch(des1, des2, k=2)
    good = []
    for pair in knn:
        if len(pair) < _MIN_KNN_PAIR:
            continue
        m, n = pair
        if m.distance < 0.75 * n.distance:
            good.append(m)
    if len(good) < _MIN_ORB_MATCHES:
        return None

    src = np.array([kp1[m.queryIdx].pt for m in good], dtype=np.float64)
    dst = np.array([kp2[m.trainIdx].pt for m in good], dtype=np.float64)
    fitted = estimate_upright_scale_translation(src, dst)
    if fitted is None:
        return None
    affine, ratio = fitted
    if ratio < _MIN_INLIER_RATIO:
        return None

    # Undo reference downscale in the transform (vp → scaled_ref → full_ref).
    if not math.isclose(scale_ref, 1.0):
        affine *= 1.0 / scale_ref

    return affine, ratio


def apply_affine(affine: np.ndarray, x: float, y: float) -> tuple[float, float]:
    nx = affine[0, 0] * x + affine[0, 1] * y + affine[0, 2]
    ny = affine[1, 0] * x + affine[1, 1] * y + affine[1, 2]
    return float(nx), float(ny)


def invert_affine(affine_vp_to_ref: np.ndarray) -> np.ndarray | None:
    """Invert a 2x3 affine (viewport→ref → ref→viewport)."""
    try:
        import cv2
    except ImportError:
        return None
    try:
        return cv2.invertAffineTransform(np.asarray(affine_vp_to_ref, dtype=np.float64))
    except cv2.error:
        return None


def ref_to_viewport(
    affine_ref_to_vp: np.ndarray, rx: float, ry: float
) -> tuple[float, float]:
    """Map reference basemap pixels → viewport-local pixels."""
    return apply_affine(affine_ref_to_vp, rx, ry)


def latlng_to_screen(
    lat: float,
    lng: float,
    *,
    meta: MapMeta,
    reg: ViewportRegistration,
) -> tuple[float, float] | None:
    """Project a marker lat/lng to physical screen pixels via registration."""
    nx, ny = latlng_to_norm(lat, lng, meta)
    rx = nx * reg.ref_width
    ry = ny * reg.ref_height
    vx, vy = ref_to_viewport(reg.affine_ref_to_vp, rx, ry)
    left, top, right, bottom = reg.viewport_bbox
    vw = right - left
    vh = bottom - top
    # Outside the visible terrain crop → skip
    if (
        vx < -_VIEWPORT_MARGIN_PX
        or vy < -_VIEWPORT_MARGIN_PX
        or vx > vw + _VIEWPORT_MARGIN_PX
        or vy > vh + _VIEWPORT_MARGIN_PX
    ):
        return None
    return left + vx, top + vy


def viewport_center_latlng(
    *,
    meta: MapMeta,
    reg: ViewportRegistration,
) -> tuple[float, float] | None:
    """Lat/lng at the center of the registered map viewport (for nearest-pin sort)."""
    left, top, right, bottom = reg.viewport_bbox
    vx = (right - left) / 2.0
    vy = (bottom - top) / 2.0
    rx, ry = apply_affine(reg.affine_vp_to_ref, vx, vy)
    if reg.ref_width <= 0 or reg.ref_height <= 0:
        return None
    nx = rx / reg.ref_width
    ny = ry / reg.ref_height
    if (
        nx < _NORM_LOOSE_MIN
        or nx > _NORM_LOOSE_MAX
        or ny < _NORM_LOOSE_MIN
        or ny > _NORM_LOOSE_MAX
    ):
        return None
    nx = min(1.0, max(0.0, nx))
    ny = min(1.0, max(0.0, ny))
    return norm_to_latlng(nx, ny, meta)


def identify_map_from_ocr(text: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", (text or "").lower()).strip()
    if not cleaned:
        return None
    for title, mid in _MAP_TITLE_TO_ID.items():
        if title in cleaned:
            return mid
    for mid, display in MAP_DISPLAY_NAMES.items():
        if display.lower() in cleaned:
            return mid
    return None


def map_viewport_bbox(full_size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Physical-pixel bbox used by ``crop_map_viewport`` (left, top, right, bottom)."""
    settings = get_settings()
    region = settings.map_viewport
    w, h = full_size
    if region.width > 1 and region.height > 1:
        left, top, right, bottom = region.bbox
        left, top = max(0, left), max(0, top)
        right, bottom = min(w, right), min(h, bottom)
        if right > left and bottom > top:
            return left, top, right, bottom
    left = int(w * 0.22)
    right = int(w * 0.78)
    top = int(h * 0.08)
    bottom = int(h * 0.92)
    return left, top, right, bottom


THUMB_SIZE = 64
# Mean abs pixel delta (0–255) on the viewport thumb that counts as pan/zoom.
# Hover tooltips / legend pulse easily exceed ~5 mean-abs; real pans are larger.
MOTION_THRESHOLD = 22.0
# Require this many consecutive "moved" polls before clearing pins.
MOTION_STREAK_REQUIRED = 2


def capture_map_thumb(
    *,
    size: int = THUMB_SIZE,
) -> np.ndarray | None:
    """Fast grayscale thumbnail of the map viewport for motion detection."""
    try:
        from .screen import get_backend

        sw, sh = get_backend().resolution()
        left, top, right, bottom = map_viewport_bbox((sw, sh))
        # Deep inset: ignore side panels, cursors, and hover popups.
        w = right - left
        h = bottom - top
        inset_x = int(w * 0.14)
        inset_y = int(h * 0.14)
        img = grab_screen(
            (left + inset_x, top + inset_y, right - inset_x, bottom - inset_y)
        )
    except Exception as e:  # ruff: ignore[blind-except]
        logger.debug("Map thumb capture failed: %s", e)
        return None
    gray = img.convert("L").resize((size, size), Image.Resampling.BILINEAR)
    return np.asarray(gray, dtype=np.uint8)


def thumbs_differ(
    a: np.ndarray | None,
    b: np.ndarray | None,
    *,
    threshold: float = MOTION_THRESHOLD,
) -> bool:
    """True when two viewport thumbs look like a pan/zoom (or one is missing)."""
    if a is None or b is None:
        return True
    if a.shape != b.shape:
        return True
    # Mean abs catches global pans; also require a solid fraction of pixels
    # changed so a small tooltip doesn't trip redraw.
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16))
    delta = float(np.mean(diff))
    if delta < threshold:
        return False
    changed = float(np.mean(diff > _THUMB_DIFF_PIXEL))
    return changed >= _THUMB_CHANGED_FRAC


def crop_map_viewport(full: Image.Image) -> Image.Image:
    """Crop center terrain, excluding typical left/right UI panels."""
    left, top, right, bottom = map_viewport_bbox(full.size)
    return full.crop((left, top, right, bottom))


class MapSyncEngine:
    """Capture open raid map; register viewport; optionally locate player."""

    def __init__(self, catalog: MapCatalog | None = None) -> None:
        self.catalog = catalog or get_map_catalog()
        self._last_fix: PlayerFix | None = None
        self._last_reg: ViewportRegistration | None = None
        self._ref_cache: dict[str, Image.Image] = {}

    @property
    def last_fix(self) -> PlayerFix | None:
        return self._last_fix

    @property
    def last_registration(self) -> ViewportRegistration | None:
        return self._last_reg

    def load_reference(self, map_id: str) -> Image.Image | None:
        if map_id in self._ref_cache:
            return self._ref_cache[map_id]
        path = reference_path(map_id)
        if not path.is_file():
            return None
        try:
            im = Image.open(path).convert("RGB")
        except OSError:
            return None
        self._ref_cache[map_id] = im
        return im

    @staticmethod
    def detect_map_open() -> bool:
        """True when the raid MAP screen is open.

        The shared top nav always shows the word INVENTORY (as an inactive tab)
        even on MAP/CRAFTING, so we must not key off that alone. Prefer
        map-specific chrome: bottom prompts (PLACE WAYPOINT) and/or a calibrated
        MAP-tab region.
        """
        settings = get_settings()
        ocr = get_ocr_engine()
        region = settings.map_trigger_region
        if region.width > 1 and region.height > 1:
            try:
                img = grab_screen(region.bbox)
            except Exception:  # ruff: ignore[blind-except]
                return False
            processed = ocr.preprocess_for_ocr(img, invert=True, scale=2)
            result = ocr.extract_text(processed, single_line=False)
            text = (result.raw_text or result.text or "").upper()
            # Calibrated region should cover the selected MAP pill / map title.
            return "MAP" in text

        try:
            from .screen import get_backend

            sw, sh = get_backend().resolution()
        except Exception:  # ruff: ignore[blind-except]
            return False

        # Bottom control bar: unique to the map UI (PAN / ZOOM / PLACE WAYPOINT).
        bottom = (
            int(sw * 0.25),
            int(sh * 0.90),
            int(sw * 0.75),
            int(sh * 0.99),
        )
        try:
            img = grab_screen(bottom)
        except Exception:  # ruff: ignore[blind-except]
            return False
        processed = ocr.preprocess_for_ocr(img, invert=True, scale=2)
        result = ocr.extract_text(processed, single_line=False)
        text = (result.raw_text or result.text or "").upper()
        text_compact = re.sub(r"\s+", "", text)
        if "WAYPOINT" in text_compact or "PLACEWAYPOINT" in text_compact:
            return True
        # Some locales / OCR drops PLACE; ZOOM+PAN together is still map-ish
        return "ZOOM" in text_compact and "PAN" in text_compact

    def _resolve_map_id(self, full: Image.Image, map_id_hint: str | None) -> str:
        # Prefer OCR of the right-panel title so a stale filter hint (e.g. dam)
        # does not win while the player is viewing Blue Gate / etc.
        map_id: str | None = None
        w, h = full.size
        title_box = (int(w * 0.78), int(h * 0.08), int(w * 0.98), int(h * 0.16))
        try:
            ocr = get_ocr_engine()
            title_img = full.crop(title_box)
            processed = ocr.preprocess_for_ocr(title_img, invert=True, scale=2)
            title_result = ocr.extract_text(processed, single_line=False)
            title_text = title_result.raw_text or title_result.text or ""
            map_id = identify_map_from_ocr(title_text)
        except Exception:  # ruff: ignore[blind-except]
            map_id = None

        if not map_id and map_id_hint:
            map_id = map_id_hint

        if not map_id and self.catalog:
            maps = self.catalog.list_maps()
            map_id = maps[0].map_id if maps else MapId.DAM
        return map_id or MapId.DAM

    @staticmethod
    def _find_player_in_viewport(
        full: Image.Image,
        viewport: Image.Image,
        bbox: tuple[int, int, int, int],
    ) -> tuple[tuple[float, float], float | None, float] | None:
        player = find_teal_player(viewport)
        if player is not None:
            return player
        player = find_teal_player(full)
        if player is None:
            return None
        (px, py), facing, score = player
        left, top, _r, _b = bbox
        return ((px - left, py - top), facing, score)

    def register_once(
        self,
        *,
        map_id_hint: str | None = None,
        detect_player: bool = False,
    ) -> ViewportRegistration | None:
        """Grab screen and register viewport→basemap.

        ``detect_player`` defaults False — the in-game pin overlay must not draw
        a teal chevron (it was re-detected as the player on the next frame).
        """
        try:
            from .screen import get_backend

            sw, sh = get_backend().resolution()
            full = grab_screen((0, 0, sw, sh))
        except Exception as e:  # ruff: ignore[blind-except]
            logger.warning("Map register capture failed: %s", e)
            return None

        bbox = map_viewport_bbox(full.size)
        viewport = full.crop(bbox)
        map_id = self._resolve_map_id(full, map_id_hint)
        meta = self.catalog.get_meta(map_id) if self.catalog else None
        reference = self.load_reference(map_id)
        if meta is None:
            logger.warning(
                "No map_meta row for %s — run update_db.py --maps-only", map_id
            )
            return None
        if reference is None:
            logger.warning(
                "No basemap image for %s — cannot register viewport for in-game pins",
                map_id,
            )
            return None

        reg_pair = register_viewport_to_reference(viewport, reference)
        if reg_pair is None:
            logger.info(
                "Map register: ORB match failed for %s "
                "(schematic/static basemaps like Pendola often mismatch in-game art)",
                map_id,
            )
            return None

        affine, ratio = reg_pair
        inv = invert_affine(affine)
        if inv is None:
            logger.info("Map register: could not invert affine for %s", map_id)
            return None

        rw, rh = reference.size
        player_fix: PlayerFix | None = None
        if detect_player:
            found = self._find_player_in_viewport(full, viewport, bbox)
            if found is not None:
                (px, py), facing, pscore = found
                rx, ry = apply_affine(affine, px, py)
                if rw > 0 and rh > 0:
                    nx = rx / rw
                    ny = ry / rh
                    if (
                        _NORM_TIGHT_MIN <= nx <= _NORM_TIGHT_MAX
                        and _NORM_TIGHT_MIN <= ny <= _NORM_TIGHT_MAX
                    ):
                        nx = min(1.0, max(0.0, nx))
                        ny = min(1.0, max(0.0, ny))
                        lat, lng = norm_to_latlng(nx, ny, meta)
                        conf = min(1.0, 0.5 * ratio + 0.5 * min(pscore / 500.0, 1.0))
                        player_fix = PlayerFix(
                            map_id=map_id,
                            lat=lat,
                            lng=lng,
                            facing=facing,
                            confidence=conf,
                            timestamp=time.time(),
                        )
                        self._last_fix = player_fix

        result = ViewportRegistration(
            map_id=map_id,
            viewport_bbox=bbox,
            affine_vp_to_ref=affine,
            affine_ref_to_vp=inv,
            confidence=float(ratio),
            ref_width=rw,
            ref_height=rh,
            timestamp=time.time(),
            player=player_fix,
        )
        self._last_reg = result
        logger.info(
            "Map register OK: %s conf=%.2f",
            map_id,
            ratio,
        )
        return result

    def sync_once(self, *, map_id_hint: str | None = None) -> PlayerFix | None:
        """Register viewport and return player fix if the teal arrow was found."""
        reg = self.register_once(map_id_hint=map_id_hint, detect_player=True)
        if reg is None:
            return None
        if reg.player is None:
            logger.info("Map sync: registered but teal player marker not found")
            return None
        return reg.player


def sync_from_image_file(
    image_path: Path,
    *,
    map_id: str,
    catalog: MapCatalog | None = None,
    meta: MapMeta | None = None,
) -> PlayerFix | None:
    """Offline helper for tests: run CV on a saved screenshot."""
    catalog = catalog or get_map_catalog()
    meta = meta or (catalog.get_meta(map_id) if catalog else None)
    if meta is None:
        return None
    full = Image.open(image_path).convert("RGB")
    bbox = map_viewport_bbox(full.size)
    left, top, _right, _bottom = bbox
    viewport = full.crop(bbox)
    engine = MapSyncEngine(catalog)
    reference = engine.load_reference(map_id)
    if reference is None:
        return None

    found_vp = find_teal_player(viewport)
    if found_vp is not None:
        (px, py), facing, pscore = found_vp
    else:
        found_full = find_teal_player(full)
        if found_full is None:
            return None
        (px, py), facing, pscore = found_full
        px -= left
        py -= top

    reg = register_viewport_to_reference(viewport, reference)
    if reg is None:
        return None
    affine, ratio = reg
    rx, ry = apply_affine(affine, px, py)
    rw, rh = reference.size
    nx = min(1.0, max(0.0, rx / rw))
    ny = min(1.0, max(0.0, ry / rh))
    lat, lng = norm_to_latlng(nx, ny, meta)
    return PlayerFix(
        map_id=map_id,
        lat=lat,
        lng=lng,
        facing=facing,
        confidence=min(1.0, 0.5 * ratio + 0.5 * min(pscore / 500.0, 1.0)),
        timestamp=time.time(),
    )
