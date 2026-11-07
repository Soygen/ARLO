# In-game map pins

ARLO caches Metaforge ARC Raiders map markers and basemap imagery locally, then projects filtered pins onto the **open in-game MAP** (click-through overlay, same stack as the tooltip).

**Data source:** [Metaforge ARC Raiders](https://metaforge.app/arc-raiders) (community data, not from Embark). Public projects must credit Metaforge and link that page. Cache responses — do not hammer the live API mid-raid. ARLO syncs maps with the normal DB update (and via `--maps-only`).

## How to use it

| Action | How |
|--------|-----|
| Show pins in-game | Open the raid **MAP** tab — status becomes `MAP` and ARLO registers the viewport ~2×/sec |
| Search / filters | **F8** — global (evdev on Wayland / GetAsyncKeyState on Windows); opens the filter window |
| Find last hovered item | **F9** sets the filter to that item’s loot areas (`MAP_FIND_HOTKEY`) |
| Leave map | Close the in-game MAP — pin overlay hides |

Status-pill clicks do **not** reach ARLO while a Wayland game has focus — use **F8**.

The top nav still shows the word `INVENTORY` on the MAP screen. ARLO detects map mode via bottom chrome (`PLACE WAYPOINT` / `PAN`+`ZOOM`) so inventory scan should not stick on.

### What appears in-game

- With a **search** or **F9 item filter**: matching pins (capped), drawn over the terrain.
- With **no search**: a small sample of the checked F8 categories (same dots as the filter preview; in-game capped ~16 on Linux).
- Pins clear while you pan/zoom, then redraw once the map stops. Leave the MAP tab to hide.

Item search merges two Metaforge sources (works even when an item has **no** `loot_area` tags — e.g. Insulation Roll):

1. **Loose spawn clusters** — the same item-view Dots feed as [Metaforge’s map](https://metaforge.app/arc-raiders/map) (e.g. Radial Press ≈308 raw spots on Pendola → ~24 clusters within ~40 world units). Cached lazily on first search (or bulk via `update_db.py --maps-only --loose-spots`).
2. **Tagged map markers** — containers / rooms whose `lootAreas` intersect the item’s loot tags (when present).

The local item index stores **all** Metaforge items (not only loot-tagged ones) so search can resolve ids/names before fetching spots.

The F8 window is for configuring search/categories and previewing results — ongoing raid use stays on the game map.

### Why F8 / overlay can feel slower than the browser

Metaforge draws dots on a GPU canvas and pans by transforming one layer. ARLO’s F8 window is Tk: pan **moves** the basemap + pin tags (no PNG re-encode per drag); zoom debounces the Pillow→PhotoImage step.

**Wayland:** in-game overlay is off (F8 only). A single chroma window becomes a black slab under XWayland; per-pin windows thrash the compositor. Native **X11** and **Windows** use one PhotoImage blit (cheap at higher pin caps). Force Wayland micro-windows with `ARLO_WAYLAND_INGAME_PINS=1` (not recommended).

Basemaps are stitched from Metaforge’s interactive map tiles on `static.metaforge.app` (`…/maps/{layer}/{z}/…`). Ingest tries `{x}/{y}.webp` first, then falls back to `{x}_{y}.webp`. Metaforge maps marker `worldExtent` onto a `tileExtent` rectangle in Leaflet zoom-0 CRS (axis-aligned affine) — ARLO uses that same transform so pins land on the stitched tile grid. Tiles often leave empty margins, so ingest **crops** those and remaps CRS to the painted pixels. Pins are drawn at true CRS positions inside that cropped image only — never edge-clamped. Some API markers sit past `worldExtent` but still on painted tiles (e.g. Spaceport Taking Stock); those stay visible. When a tile pyramid is missing, ingest falls back to `static.metaforge.app/.../custom/{map_id}.png`.

## What the pins mean

Markers come from `GET /api/game-map-data?tableID=arc_map_data&mapID=…`:

- **containers** — crates, lockers, computers, caches (tagged with loot areas such as Industrial / Medical)
- **nature** — plants and collectibles (often exact pins)
- **arc** — enemy / husk style markers
- **quests** / **locations** / **events**

Searching an **item** (e.g. Radial Press, Steel Spring) highlights every pin whose place `lootAreas` intersect that item’s Metaforge `loot_area` — containers, locked/breach rooms, and other tagged open-world spots — plus nature/quest pins whose name matches the item. Metaforge’s `ARC` tag is a drop *source*, not a map zone, so it is skipped (otherwise random Fireball/Pop pins tagged `ARC` would show for every ARC part). Enemy-named parts (e.g. Bully Fragmenter) pin that enemy instead. That is approximate drop affinity, not a guarantee.

## Position / viewport registration

1. Open the raid **MAP** screen (any zoom/pan).
2. A fast loop (~6 Hz) watches a deep-inset grayscale thumbnail of the viewport for movement (hover tooltips alone should not clear pins).
3. Sustained pan/zoom hides pins. After the map settles (~400 ms debounce), ARLO runs upright ORB once (scale and pan only — the raid map never rotates) and draws pins on Windows/X11; Wayland keeps pins in F8 only.

Limitations (same class as Metaforge’s overlay):

- Needs the map UI open — no tracking while the map is closed
- Extreme zoom / heavy UI occlusion can fail registration
- Calibrate at your native resolution for best results

### Calibration (`.env`)

```env
MAP_HOTKEY=F8
MAP_FIND_HOTKEY=F9

# Optional: OCR region covering the selected MAP tab
MAP_TRIGGER_REGION_X=0
MAP_TRIGGER_REGION_Y=0
MAP_TRIGGER_REGION_WIDTH=1
MAP_TRIGGER_REGION_HEIGHT=1

# Optional: center terrain only (exclude quests + legend sidebars)
MAP_VIEWPORT_X=0
MAP_VIEWPORT_Y=0
MAP_VIEWPORT_WIDTH=1
MAP_VIEWPORT_HEIGHT=1
```

If viewport width/height stay at `1`, ARLO uses a heuristic crop (~22% left/right margins).

## Refreshing map data

```bash
uv run python update_db.py --maps-only
uv run python update_db.py --maps-only --force-map-assets  # re-stitch basemaps
uv run python update_db.py --maps-only --loose-spots       # prefill item spawn clusters
```

Stitched basemaps (`data/maps/*/reference.png`, `overview.webp`) and `data/maps/MANIFEST.json` are committed so clones work offline. Intermediate `tiles_z*` caches stay gitignored. Ingest skips re-download when the manifest’s layer signatures still match `MAP_TILE_CONFIGS`; CI only re-stitches when those signatures change (or a reference is missing). Markers in `arlo.db` still refresh on the normal update.
