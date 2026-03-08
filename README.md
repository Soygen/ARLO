# PabsArcTooltip Deluxe

An enhanced fork of [PabsArcTooltip](https://github.com/Pabosik/PabsArcTooltip) - a screen overlay tool for [Arc Raiders](https://store.steampowered.com/app/2073750/ARC_Raiders/) that detects items in your inventory and displays recommended actions (**keep**, **recycle**, **sell**) along with sell prices, stack sizes, and crafting details.

This fork adds automatic item database updates from the [Arc Raiders Wiki](https://arcraiders.wiki/wiki/Loot), sell price and stack size info on the overlay, and auto-sync on every launch.

[![Example 1](static/screen_01.png)](static/screen_01.png)
[![Example 2](static/screen_02.png)](static/screen_02.png)

---

## What's New vs. the Original

- **Auto-sync on launch** - The item database updates from the wiki every time you start the app (throttled to once per 24 hours). No manual steps needed.
- **Sell price & stack size on the overlay** - See at a glance what an item sells for and how high it stacks
- **300+ items** - Expanded coverage including keys, mods, augments, shields, ammo, and consumables
- **Smart action generation** - Items are auto-categorized (keep materials, sell trinkets, recycle junk) based on wiki data
- **Merge mode** - New items from the wiki are added without overwriting your manual action overrides
- **GitHub Actions** - Optional scheduled workflow that auto-updates the database weekly

---

## How It Works

The tool runs two detection phases while you play:

1. **Trigger Detection** (every 500ms) - Scans for the word "INVENTORY" on screen to know when your inventory is open
2. **Tooltip Detection** (every 300ms) - When inventory is open, captures the area around your cursor, reads the item name via OCR, looks it up in the database, and shows an overlay

The overlay popup displays:

```
+--------------------------------------+
|  ARC Alloy                           |
|  -> Keep                             |
|  Sell: 200   Stack: 15               |
|  For: Workshop Explosives Station 1  |
+--------------------------------------+
```

- **Item name** at the top
- **Action** color-coded (green = keep, gold = sell, turquoise = recycle, pink = use)
- **Sell price** and **stack size** on the info line
- **Details** - what it recycles into, or why you're keeping it

---

## Requirements

### Pre-built Release
- Windows 10/11
- Arc Raiders running in **borderless windowed** or **windowed** mode (not exclusive fullscreen)

### Running from Source
- Windows 10/11
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)

---

## Installation

### Option 1: Pre-built Release (Recommended)

1. Download the latest release from the [Releases](https://github.com/Soygen/PabsArcTooltipDeluxe/releases) page
2. Extract the zip to a folder of your choice
3. Run `ArcRaidersHelper.exe`

The release includes all dependencies, including Tesseract OCR. The item database updates automatically from the wiki on first launch.

### Option 2: From Source

1. Clone the repository:
   ```
   git clone https://github.com/Soygen/PabsArcTooltipDeluxe.git
   cd PabsArcTooltipDeluxe
   ```

2. Install dependencies:
   ```
   uv sync --all-extras
   ```

3. Install [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) to `C:\Program Files\Tesseract-OCR\` (or set `TESSERACT_PATH` in `.env` if installed elsewhere)

4. Copy the example config:
   ```
   copy .env.example .env
   ```

5. Launch the app:
   ```
   uv run arc-helper
   ```

The item database updates automatically from the wiki on first launch. No separate step needed.

---

## Item Database

### Automatic Updates

The app checks the [Arc Raiders Wiki loot table](https://arcraiders.wiki/wiki/Loot) for updates every time it starts, throttled to once per 24 hours. This happens silently in the background using merge mode, so any manual action overrides you've made are preserved and new items are added automatically.

If the wiki is unreachable (no internet, site down, etc.), the app continues normally with whatever database it already has.

### Manual Update

You can also trigger a database update manually from the command line:

```
uv run python update_db.py              # Full update from wiki
uv run python update_db.py --merge      # Keep your manual overrides
uv run python update_db.py --dry-run    # Preview changes without writing
```

Or use the makefile shortcuts:
```
make update-db          # Full update
make update-db-merge    # Merge mode
make update-db-dry      # Dry run preview
```

### GitHub Actions (Optional)

The included workflow at `.github/workflows/update-db.yml` can also sync the database automatically:

- Runs weekly on Mondays at 6:00 UTC in merge mode
- Auto-commits updated `items.csv` and `items.db` if changes are detected
- Can be triggered manually from the **Actions** tab on GitHub

No secrets or extra configuration needed.

For more details on how the scraper works, CSV format, and manual database management, see [docs/ITEMS.md](docs/ITEMS.md).

---

## Building the Standalone Executable

To produce a distributable package with everything bundled:

```
uv sync --all-extras
uv run python build.py
```

Output lands in `dist/ArcRaidersHelper/` containing the exe, calibration tool, bundled Tesseract, config files, item database, and the wiki updater script. Zip that folder to share with anyone - no Python install needed on their end.

See [docs/BUILD.md](docs/BUILD.md) for full build details.

---

## First Run Setup

On first launch the app detects your screen resolution and loads a matching profile if one exists:

| Resolution | Aspect Ratio | Status |
|---|---|---|
| 5120x2160 | 21:9 DQHD Ultrawide | ✅ Configured |
| 3840x2160 | 16:9 4K UHD | ✅ Configured |
| 3440x1440 | 21:9 Ultrawide QHD | ✅ Configured |
| 2560x1440 | 16:9 QHD | ✅ Configured |
| 2560x1080 | 21:9 Ultrawide FHD | ✅ Configured |
| 1920x1080 | 16:9 Full HD | ✅ Configured |

If your resolution isn't listed, run the [Calibration tool](docs/CALIBRATION.md):

```
uv run arc-calibrate
```

---

## Configuration

All settings live in the `.env` file. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for details on scan regions, overlay position, display time, and debug mode.

---

## Project Structure

```
PabsArcTooltipDeluxe/
├── update_db.py                    # Wiki scraper & DB updater
├── items.csv                       # Item data (auto-generated or hand-edited)
├── items.db                        # SQLite database (built from CSV)
├── pyproject.toml                  # Package config & dependencies
├── makefile                        # Convenience targets
├── build.py                        # PyInstaller build script
├── .env.example                    # Configuration template
├── .github/workflows/
│   └── update-db.yml               # Scheduled wiki sync
├── src/arc_helper/
│   ├── main.py                     # Main app entry point
│   ├── config.py                   # Settings & logging
│   ├── database.py                 # SQLite + CSV import + fuzzy matching
│   ├── ocr.py                      # Screen capture & Tesseract OCR
│   ├── overlay.py                  # Tkinter overlay (action + price + stack)
│   ├── calibrate.py                # GUI calibration tool
│   ├── resolution_profiles.py      # Resolution presets
│   └── resolutions.json            # Pre-configured resolution profiles
├── docs/
│   ├── ITEMS.md                    # Item database & wiki sync docs
│   ├── CALIBRATION.md              # Calibration guide
│   ├── CONFIGURATION.md            # Settings reference
│   ├── BUILD.md                    # Build instructions
│   ├── TROUBLESHOOTING.md          # Common issues
│   └── CHANGELOG.md                # Version history
└── static/                         # Screenshots
```

---

## Contributing

### Item Database
Run `uv run python update_db.py --dry-run` to preview the wiki data. If you spot incorrect auto-generated actions, edit `items.csv` directly and use `--merge` on future updates to preserve your fixes.

### Resolution Profiles
If you calibrate for a resolution that isn't pre-configured, add it to `resolutions.json` and submit a PR.

### Bug Reports
Please include your screen resolution, debug images from the `debug/` folder (enable `DEBUG_MODE=true` in `.env`), and `arc_helper.log`.

---

## Credits

- **[PabsArcTooltip](https://github.com/Pabosik/PabsArcTooltip)** - Original project by [Pabosik](https://github.com/Pabosik)
- **[Arc Raiders Wiki](https://arcraiders.wiki)** - Community wiki used as the item data source
- **[Tesseract OCR](https://github.com/tesseract-ocr/tesseract)** - Text recognition engine
- **[PyInstaller](https://pyinstaller.org/)** - Executable packaging

---

## License

MIT License - Free to use and modify.
