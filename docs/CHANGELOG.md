# Changelog

All notable changes to this project will be documented in this file.

## Versions:

### [0.4.0]

Stable cut of this fork ([jaredcat/arlo-plus](https://github.com/jaredcat/arlo-plus)), following Soygen’s `v0.3.0` line. Qt UI and in-game map pins remain on `feat/map-overlay` for a future 1.0.0.

- Hybrid catalog sync: MetaForge for items/quests/prices; wiki for workshop/projects/traders
- Player progress import from MetaForge (bookmarklet → `account-sync.json`) filters finished keep reasons
- Show sell price on the overlay only when the in-raid inventory trigger matches
- Linux X11 / Wayland capture support
- Absorb Soygen and Pabosik fork ancestry while keeping `data/` + `arlo.db` layout
- GitHub Releases: `main` = stable, `dev` = beta pre-releases

### [0.1.1]
- Performance improvements
- Code cleanup

### [0.1.0]
- Initial version of the project
- Base OCR tool
- Calibration tool
- Resolution profiles
- Documentation
- Items database + management (via calibration tool)
