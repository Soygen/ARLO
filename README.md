# Arc Raiders Helper (Wiki-Sync Fork)

A screen overlay tool for Arc Raiders that detects items in your inventory and shows recommended actions — **keep**, **recycle**, **sell** — along with sell prices, stack sizes, and crafting details. This fork adds automatic database updates from the [Arc Raiders Wiki](https://arcraiders.wiki/wiki/Loot).

[![Example 1](static/screen_01.png)](static/screen_01.png)
[![Example 2](static/screen_02.png)](static/screen_02.png)

---

## What's New in This Fork

- **Wiki auto-sync** — Item database updates automatically from the Arc Raiders Wiki loot table
- **Sell price & stack size** — Shown directly on the in-game overlay popup
- **300+ items** — Expanded coverage including keys, mods, augments, shields, ammo, and consumables
- **Merge mode** — Pull new items from the wiki without losing your manual action overrides
- **GitHub Actions** — Optional weekly auto-update that commits changes to your repo

---

## How It Works

The tool runs two detection phases while you play:

1. **Trigger Detection** (every 500ms) — Scans for the word "INVENTORY" on screen to know when your inventory is open
2. **Tooltip Detection** (every 300ms) — When inventory is open, captures the area around your cursor, reads the item name via OCR, looks it up in the database, and shows an overlay with the recommendation

The overlay popup shows:
- **Item name**
- **Action** (Keep / Recycle / Sell / Use) in color
- **Sell price** and **stack size**
- **Details** — what it recycles into or why you're keeping it

---

## Requirements

### Pre-built Release
- Windows 10/11
- Arc Raiders in **borderless windowed** or **windowed** mode

### From Source
- Windows 10/11
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)

---

## Installation

### Option 1: Pre-built Release

1. Download from [Releases](https://github.com/yourusername/arc-raiders-helper/releases)
2. Extract and run `ArcRaidersHelper.exe`

### Option 2: From Source

```bash
git clone https://github.com/yourusername/arc-raiders-helper.git
cd arc-raiders-helper
uv sync
cp .env.example .env
uv run arc-helper
```

Install Tesseract OCR to `C:\Program Files\Tesseract-OCR\` or set `TESSERACT_PATH` in `.env`.

---

## Updating the Item Database

### Quick Update

```bash
# Install scraper dependencies (first time only)
uv sync --extra scraper

# Full update from wiki
python update_db.py

# Or use make
make update-db
```

### Merge Mode (Preserve Your Overrides)

If you've tweaked action recommendations for specific items and want to keep those while pulling new items from the wiki:

```bash
python update_db.py --merge
```

### Preview Changes

```bash
python update_db.py --dry-run
```

### Automatic Updates via GitHub Actions

This fork includes a workflow at `.github/workflows/update-db.yml` that:
- Runs **weekly** (Mondays at 6:00 UTC) in merge mode
- Auto-commits updated `items.csv` and `items.db` if changes are found
- Can be triggered manually from the **Actions** tab

To enable it, just push the workflow file to your fork — no secrets or configuration needed.

See [Item Database docs](docs/ITEMS.md) for full details on how the scraper works and how to manage the CSV manually.

---

## First Run Setup

On first launch the app detects your screen resolution and loads a matching profile if available:

| Resolution | Aspect Ratio | Status |
|---|---|---|
| 3440×1440 | 21:9 Ultrawide QHD | ✅ Configured |
| 2560×1440 | 16:9 QHD | ✅ Configured |
| 1920×1080 | 16:9 Full HD | ✅ Configured |
| 3840×2160 | 16:9 4K UHD | ⚠️ Needs calibration |
| 2560×1080 | 21:9 Ultrawide FHD | ✅ Configured |

If your resolution isn't listed, run the [Calibration tool](docs/CALIBRATION.md) to configure it.

---

## Configuration

All settings are stored in `.env`. See [Configuration docs](docs/CONFIGURATION.md) for details on scan regions, overlay position, display time, and debug mode.

---

## Project Structure

```
arc-raiders-helper/
├── update_db.py                    # Wiki scraper & DB updater
├── items.csv                       # Item data (auto-generated or hand-edited)
├── items.db                        # SQLite database (built from CSV)
├── pyproject.toml                  # Package config & dependencies
├── makefile                        # Convenience targets
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
│   └── resolutions.json            # Pre-configured profiles
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
Run `python update_db.py --dry-run` to see what the wiki has. If you spot incorrect auto-generated actions, edit `items.csv` and use `--merge` on future updates to preserve your fixes.

### Resolution Profiles
Calibrate for your resolution and submit the values in a PR.

### Bug Reports
Include your screen resolution, debug images from `debug/`, and `arc_helper.log`.

---

## Credits

- [PabsArcTooltip](https://github.com/Pabosik/PabsArcTooltip) — Original project by Pabosik
- [Arc Raiders Wiki](https://arcraiders.wiki) — Item data source
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) — Text recognition
- [PyInstaller](https://pyinstaller.org/) — Executable packaging

## License

MIT License — Free to use and modify.
