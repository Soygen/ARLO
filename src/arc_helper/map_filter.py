"""Shared map search/filter state for config window + in-game pin overlay."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from .map_data import CATEGORIES
from .map_data import MapCatalog
from .map_data import MapId
from .map_data import MapMarker
from .map_data import MapMeta
from .map_data import latlng_on_reference

# Cap for in-game overlay (single Qt surface; cheap even at higher counts).
INGAME_PIN_CAP = 300


@dataclass
class MapFilterState:
    """Search + category filters shared by MapWindow and in-game pins."""

    map_id: str = MapId.DAM
    search: str = ""
    item_filter: str | None = None
    categories: set[str] = field(default_factory=lambda: set(CATEGORIES))
    locked_only: bool = False

    def snapshot(self) -> MapFilterState:
        return MapFilterState(
            map_id=self.map_id,
            search=self.search,
            item_filter=self.item_filter,
            categories=set(self.categories),
            locked_only=self.locked_only,
        )


def _sample(markers: list[MapMarker], limit: int) -> list[MapMarker]:
    """Return up to ``limit`` markers, spaced across the list when over cap."""
    if limit <= 0:
        return []
    if len(markers) <= limit:
        return list(markers)
    if limit == 1:
        return [markers[0]]
    # Evenly spaced indices so we always return exactly ``limit`` pins.
    return [markers[int(i * (len(markers) - 1) / (limit - 1))] for i in range(limit)]


def resolve_markers(
    catalog: MapCatalog,
    state: MapFilterState,
    *,
    origin: tuple[float, float] | None = None,
    limit: int = INGAME_PIN_CAP,
    meta: MapMeta | None = None,
) -> list[MapMarker]:
    """Query markers for F8 preview and the in-game overlay.

    With a search / item filter: loose spawn clusters + loot-area tags, plus
    free-text name hits. With no search: a capped sample of the checked
    categories (same dots F8 shows) so the overlay matches the filter window.

    When ``meta`` is set, markers outside the stitched basemap CRS are dropped
    before sampling so F8 / overlay budgets are not wasted on off-image points.
    """
    map_id = state.map_id
    search = (state.search or "").strip() or None
    item = (state.item_filter or "").strip() or None

    def _on_basemap(markers: list[MapMarker]) -> list[MapMarker]:
        if meta is None:
            return markers
        # Category browsing: sample pins that project onto the painted basemap
        # CRS (after content-crop). Never edge-clamp — that invents positions.
        # Search/item paths below keep every hit; map_window still only draws
        # markers inside 0..1 of the reference image.
        return [
            m for m in markers if latlng_on_reference(m.lat, m.lng, meta, margin=0.35)
        ]

    if not search and not item:
        markers = catalog.query_markers(
            map_id,
            categories=state.categories or None,
            behind_locked_only=state.locked_only,
            origin=origin,
            limit=None,
        )
        return _sample(_on_basemap(markers), limit)

    needle = item or search
    assert needle is not None
    # Canonicalize against the full Metaforge item index (loot tags optional).
    ref = None
    if hasattr(catalog, "resolve_item_ref"):
        ref = catalog.resolve_item_ref(needle, network=False)
    item_needle = ref[1] if ref else needle
    markers = catalog.markers_for_item(map_id, item_needle, origin=origin, limit=None)
    seen = {m.id for m in markers}

    # Free-text: also match marker names / ARC aliases that aren't item-loot.
    if search:
        for m in catalog.query_markers(
            map_id,
            categories=None,
            search=search,
            behind_locked_only=False,
            origin=origin,
            limit=None,
        ):
            if m.id not in seen:
                seen.add(m.id)
                markers.append(m)

    if state.locked_only:
        markers = [m for m in markers if m.behind_locked_door]
    return markers[:limit]
