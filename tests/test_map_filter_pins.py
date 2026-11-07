"""Filter resolution + affine invert / latlng projection helpers."""

from __future__ import annotations

import numpy as np
import pytest

from arc_helper.map_data import MapMarker
from arc_helper.map_data import MapMeta
from arc_helper.map_filter import INGAME_PIN_CAP
from arc_helper.map_filter import MapFilterState
from arc_helper.map_filter import resolve_markers
from arc_helper.map_sync import ViewportRegistration
from arc_helper.map_sync import apply_affine
from arc_helper.map_sync import invert_affine
from arc_helper.map_sync import latlng_to_screen
from arc_helper.map_sync import ref_to_viewport


def test_invert_affine_round_trip():
    # Scale 2, translate (10, 20)
    affine = np.array([[2.0, 0.0, 10.0], [0.0, 2.0, 20.0]], dtype=np.float64)
    inv = invert_affine(affine)
    assert inv is not None
    rx, ry = apply_affine(affine, 5.0, 7.0)
    vx, vy = ref_to_viewport(inv, rx, ry)
    assert abs(vx - 5.0) < 1e-6
    assert abs(vy - 7.0) < 1e-6


def test_upright_scale_translation_recovers_pan_zoom():
    from arc_helper.map_sync import estimate_upright_scale_translation

    rng = np.random.default_rng(0)
    src = rng.uniform(0.0, 200.0, size=(40, 2))
    scale, tx, ty = 1.7, 12.0, -4.0
    dst = scale * src + np.array([tx, ty])
    dst[0] = (0.0, 0.0)  # one outlier
    fitted = estimate_upright_scale_translation(src, dst)
    assert fitted is not None
    affine, ratio = fitted
    assert affine[0, 0] == pytest.approx(scale, abs=0.05)
    assert affine[1, 1] == pytest.approx(scale, abs=0.05)
    assert affine[0, 1] == pytest.approx(0.0)
    assert affine[1, 0] == pytest.approx(0.0)
    assert affine[0, 2] == pytest.approx(tx, abs=1.0)
    assert affine[1, 2] == pytest.approx(ty, abs=1.0)
    assert ratio > 0.8


def test_upright_scale_translation_rejects_half_turn():
    from arc_helper.map_sync import estimate_upright_scale_translation

    rng = np.random.default_rng(1)
    src = rng.uniform(0.0, 200.0, size=(30, 2))
    dst = -1.5 * src + np.array([80.0, 40.0])
    assert estimate_upright_scale_translation(src, dst, reproj_thresh=3.0) is None


def test_latlng_to_screen_projects_into_viewport():
    meta = MapMeta(
        map_id="test",
        display_name="Test",
        lat_min=0.0,
        lat_max=100.0,
        lng_min=0.0,
        lng_max=100.0,
        marker_count=0,
        ref_width=200,
        ref_height=200,
    )
    # Identity-ish: vp → ref is scale 1, offset 0; ref→vp same
    affine = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)
    inv = invert_affine(affine)
    assert inv is not None
    reg = ViewportRegistration(
        map_id="test",
        viewport_bbox=(100, 50, 300, 250),
        affine_vp_to_ref=affine,
        affine_ref_to_vp=inv,
        confidence=0.9,
        ref_width=200,
        ref_height=200,
    )
    # Center of map → norm (0.5, 0.5) → ref (100, 100) → screen (200, 150)
    screen = latlng_to_screen(50.0, 50.0, meta=meta, reg=reg)
    assert screen is not None
    sx, sy = screen
    assert abs(sx - 200.0) < 1e-6
    assert abs(sy - 150.0) < 1e-6

    from arc_helper.map_sync import viewport_center_latlng

    center = viewport_center_latlng(meta=meta, reg=reg)
    assert center is not None
    assert abs(center[0] - 50.0) < 1e-6
    assert abs(center[1] - 50.0) < 1e-6


class _FakeCatalog:
    def __init__(self, markers: list[MapMarker]):
        self._markers = markers

    def loot_areas_for_item(self, _name: str) -> list[str]:
        return []

    def markers_for_item(self, map_id, item_name, **kwargs):
        # Simulate loose/item hits only when the name matches a marker label.
        needle = (item_name or "").lower()
        out = [
            m
            for m in self._markers
            if m.map_id == map_id
            and (
                needle in (m.instance_name or "").lower()
                or needle in (m.subcategory or "").lower()
            )
        ]
        limit = kwargs.get("limit")
        if limit is not None:
            out = out[:limit]
        return out

    def query_markers(self, map_id, **kwargs):
        cats = kwargs.get("categories")
        search = kwargs.get("search")
        origin = kwargs.get("origin")
        limit = kwargs.get("limit")
        out = [m for m in self._markers if m.map_id == map_id]
        if cats:
            out = [m for m in out if m.category in cats]
        if search:
            needle = search.lower()
            out = [
                m
                for m in out
                if needle in (m.instance_name or "").lower()
                or needle in (m.subcategory or "").lower()
            ]
        if origin is not None:
            olat, olng = origin
            for m in out:
                m.distance = abs(m.lat - olat) + abs(m.lng - olng)
            out.sort(key=lambda m: m.distance or 0)
        if limit is not None:
            out = out[:limit]
        return out


def _mk(i: int, *, cat: str = "containers") -> MapMarker:
    return MapMarker(
        id=f"m{i}",
        map_id="dam",
        lat=float(i),
        lng=float(i),
        category=cat,
        subcategory="crate",
        instance_name=f"pin-{i}",
    )


def test_resolve_markers_category_sample_without_search():
    """No search → capped sample of checked categories (F8 + in-game match)."""
    cat = _FakeCatalog([_mk(i) for i in range(20)])
    state = MapFilterState(map_id="dam", search="", categories={"containers"})
    out = resolve_markers(cat, state, origin=None, limit=10)
    assert len(out) == 10
    assert all(m.category == "containers" for m in out)
    cat_big = _FakeCatalog([_mk(i) for i in range(INGAME_PIN_CAP + 50)])
    out2 = resolve_markers(cat_big, state, origin=(0.0, 0.0), limit=INGAME_PIN_CAP)
    assert len(out2) == INGAME_PIN_CAP


def test_resolve_markers_drops_off_basemap_when_meta_given():
    """Markers far past the stitched reference CRS are excluded before the cap."""
    markers = [_mk(i) for i in range(20)]
    # lat/lng == i; basemap covers 0..5 (margin 0.35 keeps ~0..6.75)
    meta = MapMeta(
        map_id="dam",
        lat_min=0.0,
        lat_max=5.0,
        lng_min=0.0,
        lng_max=5.0,
    )
    cat = _FakeCatalog(markers)
    state = MapFilterState(map_id="dam", search="", categories={"containers"})
    out = resolve_markers(cat, state, origin=None, limit=50, meta=meta)
    assert len(out) == 7  # i = 0..6 within margin
    assert all(m.lat <= 7.0 and m.lng <= 7.0 for m in out)
    assert all(m.lat < 10 for m in out)


def test_resolve_markers_search_text():
    cat = _FakeCatalog([_mk(1), _mk(2), _mk(3)])
    state = MapFilterState(map_id="dam", search="pin-2", categories={"containers"})
    out = resolve_markers(cat, state, origin=None)
    assert len(out) == 1
    assert out[0].instance_name == "pin-2"


def test_resolve_search_ignores_category_checkboxes():
    """Typed search rebuilds the pin set from the catalog, not the checked cats."""
    markers = [
        _mk(1, cat="containers"),
        MapMarker(
            id="arc1",
            map_id="dam",
            lat=1.0,
            lng=1.0,
            category="arc",
            subcategory="bison",
            instance_name="",
        ),
    ]
    _FakeCatalog(markers)
    # Only containers checked — search should still find the ARC pin by alias.
    MapFilterState(map_id="dam", search="leaper", categories={"containers"})
    # FakeCatalog doesn't expand aliases; exercise expand_search_needles separately.
    from arc_helper.map_data import expand_search_needles
    from arc_helper.map_data import marker_label

    needles = expand_search_needles("leaper")
    assert "bison" in needles
    assert marker_label(markers[1]) == "Leaper"

    # With alias-aware query simulation:
    out = [
        m
        for m in markers
        if any(
            n.lower() in (m.instance_name or "").lower()
            or n.lower() in (m.subcategory or "").lower()
            for n in needles
        )
    ]
    assert len(out) == 1
    assert out[0].subcategory == "bison"


def test_expand_search_needles_leaper():
    from arc_helper.map_data import expand_search_needles

    assert "bison" in expand_search_needles("Leaper")
    assert "bison" in expand_search_needles("leap")
    assert "rollbot" in expand_search_needles("surveyor")


def test_expand_search_needles_quest_slug_separators():
    from arc_helper.map_data import expand_search_needles

    needles = expand_search_needles("what we left behind")
    assert "what-we-left-behind" in needles
    assert "what_we_left_behind" in needles


def test_quest_marker_labels_share_title():
    from arc_helper.map_data import MapMarker
    from arc_helper.map_data import marker_label

    objective = MapMarker(
        id="q1",
        map_id="buried-city",
        lat=1.0,
        lng=1.0,
        category="quests",
        subcategory="what-we-left-behind",
        instance_name="Search 2 containers in the Raider Camp",
    )
    bare = MapMarker(
        id="q2",
        map_id="buried-city",
        lat=2.0,
        lng=2.0,
        category="quests",
        subcategory="what-we-left-behind",
        instance_name="",
    )
    assert marker_label(bare) == "What We Left Behind"
    assert marker_label(objective) == (
        "What We Left Behind — Search 2 containers in the Raider Camp"
    )
