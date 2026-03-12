## Item Database

Items are stored in `items.db` (SQLite database) with the following columns: `name`, `action`, `recycle_for`, `keep_for`, `sell_price`, and `stack_size`. The database updates automatically from the [MetaForge API](https://metaforge.app/arc-raiders) on every app launch, with the [Arc Raiders Wiki](https://arcraiders.wiki/wiki/Loot) as a fallback.

### Automatic Updates on Launch

Every time `ARLO.exe` (or `uv run arc-helper`) starts, it checks for item database updates. This is throttled to once per 24 hours so it doesn't slow down repeated launches. The update runs in merge mode, meaning your manual action overrides are always preserved.

The updater pulls data from three MetaForge sources:

- **Items API** (`/api/arc-raiders/items`) - Item names, categories, sell prices, stack sizes, rarity
- **Recycle components** (Supabase `arc_item_recycle_components`) - What each item recycles into
- **Quests API** (`/api/arc-raiders/quests`) - Which items are needed for which quests

If MetaForge is unreachable, the updater automatically falls back to scraping the Arc Raiders Wiki. If both are down, the app starts normally with whatever database it already has.

The throttle timestamp is stored in `.last_wiki_update` in the app directory. Delete this file to force an update on the next launch.

### Manual Update from Command Line

You can also run the updater directly for more control:

```bash
# Full update from MetaForge API (default)
uv run python update_db.py

# Merge mode - keeps your manual overrides
uv run python update_db.py --merge

# Dry run - preview changes without writing any files
uv run python update_db.py --dry-run

# Force wiki scraper instead of API
uv run python update_db.py --source wiki

# CSV only - update the CSV without rebuilding the database
uv run python update_db.py --csv-only
```

Or use the makefile shortcuts:
```bash
make update-db          # Full update
make update-db-merge    # Merge mode
make update-db-dry      # Dry run preview
```

### How Action Generation Works

The updater auto-generates action recommendations based on item category and uses:

- **Basic/Refined/Topside Materials** - `Keep` (crafting ingredients)
- **Trinkets** with no uses - `Sell`
- **Recyclables** with workshop/quest uses - `Keep until uses complete; recycle after`
- **Recyclables** with no uses - `Recycle`
- **Keys** - `Keep`
- **Quick Use / Mods / Augments / Shields** - `Keep` or `Use`
- Items needed for quests get a "Quests:" note in the keep_for field

### Merge Mode

The on-launch auto-update always uses merge mode. When you run the updater manually, you can choose between full update or merge mode.

In merge mode the updater will:
- **Preserve** your existing `action`, `recycle_for`, and `keep_for` for items already in your CSV
- **Add** any new items with auto-generated recommendations
- **Fill in** empty `recycle_for` / `keep_for` fields from API data
- **Always update** `sell_price` and `stack_size` to the latest values

### GitHub Actions (Optional)

If you fork this repo, the included GitHub Actions workflow at `.github/workflows/update-db.yml` can sync the database on a schedule:

- Runs weekly on Mondays at 6:00 UTC in merge mode
- Auto-commits updated `items.csv` and `items.db` if changes are detected
- Can be triggered manually from the **Actions** tab on GitHub

No secrets or configuration needed - just push the workflow file and it works.

---

### Manual CSV Management

You can also edit `items.csv` directly. The CSV has the following columns:

```csv
name,action,recycle_for,keep_for,sell_price,stack_size
Advanced Arc Powercell,Keep,2x ARC Powercell,Medical Lab: Surge Shield Recharger,640,5
ARC Alloy,Keep,2x Metal Parts,Workshop Explosives Station 1 (6x),200,15
Air Freshener,Sell,,,,2000,5
Alarm Clock,Recycle,"1x Processor, 6x Plastic Parts",,1000,3
```

| Column | Description |
|--------|-------------|
| `name` | Item name as it appears in-game (case-insensitive matching) |
| `action` | Recommended action: `KEEP`, `RECYCLE`, `SELL`, `USE`, `TRASH`, or any custom text |
| `recycle_for` | What you get when recycling (shown when action is RECYCLE) |
| `keep_for` | Why to keep it (shown when action is KEEP or USE) |
| `sell_price` | Sell value in credits (shown on overlay) |
| `stack_size` | Maximum stack size (shown on overlay) |

The `sell_price` and `stack_size` columns are optional for backwards compatibility. If missing, the overlay just won't show that info for those items.

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
- If you customize an action and want to keep it, use `--merge` mode (or just let the on-launch auto-update handle it - it always uses merge mode)
