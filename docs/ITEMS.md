## Item Database

Items are stored in `items.db` (SQLite database). The database can be updated automatically from the [Arc Raiders Wiki](https://arcraiders.wiki/wiki/Loot) or managed manually via CSV.

### Automatic Update from Wiki

The easiest way to keep the item database current is to run the wiki scraper:

```bash
# Install scraper dependencies (one time)
uv sync --extra scraper

# Full update — overwrites items.csv and rebuilds items.db
python update_db.py

# Merge mode — pulls new items from wiki but keeps your manual overrides
python update_db.py --merge

# Dry run — preview changes without writing any files
python update_db.py --dry-run

# CSV only — update the CSV without rebuilding the database
python update_db.py --csv-only
```

Or use the makefile shortcuts:
```bash
make update-db          # Full update
make update-db-merge    # Merge mode
make update-db-dry      # Dry run preview
```

#### How Auto-Update Works

The scraper:
1. Fetches the loot table from `https://arcraiders.wiki/wiki/Loot`
2. Parses every item's **name**, **rarity**, **recycles to**, **sell price**, **stack size**, **category**, and **uses**
3. Auto-generates action recommendations based on category and uses:
   - **Basic/Refined/Topside Materials** → `Keep` (crafting ingredients)
   - **Trinkets** with no uses → `Sell`
   - **Recyclables** with workshop/quest uses → `Keep until uses complete; recycle after`
   - **Recyclables** with no uses → `Recycle`
   - **Keys** → `Keep`
   - **Quick Use / Mods / Augments / Shields** → `Keep` or `Use`
4. Writes `items.csv` and rebuilds `items.db`

#### Merge Mode

Use `--merge` when you've customized action recommendations and don't want to lose them. The scraper will:
- **Keep** your existing `action`, `recycle_for`, and `keep_for` for items already in your CSV
- **Add** any new items from the wiki with auto-generated recommendations
- **Fill in** empty `recycle_for` / `keep_for` fields from wiki data

#### GitHub Actions (Optional)

If you fork this repo, you can enable the included GitHub Actions workflow (`.github/workflows/update-db.yml`) to automatically check for wiki updates on a schedule. See the workflow file for configuration details.

---

### Manual CSV Management

Edit `items.csv` with the following columns:
```csv
name,action,recycle_for,keep_for
Advanced Arc Powercell,Keep,2x ARC Powercell,Medical Lab: Surge Shield Recharger
ARC CIRCUITRY,RECYCLE,ARC Alloy,
BASIC ELECTRONICS,RECYCLE,Basic components,
MEDICAL SUPPLIES,USE,,Emergency healing
```

| Column | Description |
|--------|-------------|
| `name` | Item name as it appears in-game (case-insensitive matching) |
| `action` | Recommended action: `KEEP`, `RECYCLE`, `SELL`, `USE`, `TRASH`, or any custom text |
| `recycle_for` | What you get when recycling (shown when action is RECYCLE) |
| `keep_for` | Why to keep it (shown when action is KEEP or USE) |

### Managing the Database via Calibration Tool

1. Edit `items.csv` in Excel, Google Sheets, or any text editor
2. Run `Calibrate.exe` (or `uv run arc-calibrate`)
3. In the "Item Database" section at the bottom:
   - Click **"Load CSV..."** to import your updated CSV file
   - Click **"View Items"** to see all items currently in the database
   - Click **"Clear Database"** to remove all items and start fresh

The database is automatically created on first run. Loading a CSV file will replace all existing items.

### Tips for the CSV File

- You can use Excel or Google Sheets to edit the CSV - just make sure to save as CSV format
- Item names are matched case-insensitively (e.g., "Arc Circuitry" will match "ARC CIRCUITRY")
- Leave `recycle_for` or `keep_for` empty if not applicable
- The `action` field can be any text - it will be displayed as-is on the overlay
