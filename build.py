"""Build script to create standalone executable."""

import logging
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

# Setup logging for build script
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("build")

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
OUTPUT = DIST / "ArcRaidersHelper"
TESSERACT_SRC = Path(r"C:\Program Files\Tesseract-OCR")


def build():
    """Build the executable."""
    logger.info("Starting build process...")

    # Clean previous builds
    if DIST.exists():
        logger.info("Cleaning previous dist folder...")
        shutil.rmtree(DIST)
    if BUILD.exists():
        logger.info("Cleaning previous build folder...")
        shutil.rmtree(BUILD)

    # Build main app using spec file
    logger.info("Building main application...")
    PyInstaller.__main__.run(
        [
            "ArcRaidersHelper.spec",
            "--clean",
        ]
    )
    logger.info("Main application built successfully")

    # Build calibration tool
    logger.info("Building calibration tool...")
    PyInstaller.__main__.run(
        [
            "src/arc_helper/calibrate.py",
            "--name=Calibrate",
            "--onedir",
            "--windowed",
            "--distpath",
            str(DIST),
            "--clean",
        ]
    )
    logger.info("Calibration tool built successfully")

    # Move calibrate exe to main folder
    calibrate_dir = DIST / "Calibrate"
    if calibrate_dir.exists():
        calibrate_exe = calibrate_dir / "Calibrate.exe"
        if calibrate_exe.exists():
            logger.info("Moving Calibrate.exe to output folder...")
            shutil.copy(calibrate_exe, OUTPUT / "Calibrate.exe")
        shutil.rmtree(calibrate_dir)

    # Copy user-editable files
    logger.info("Copying configuration files...")
    shutil.copy(ROOT / ".env.example", OUTPUT / ".env.example")
    shutil.copy(ROOT / ".env.example", OUTPUT / ".env")
    shutil.copy(ROOT / "items.csv", OUTPUT / "items.csv")
    shutil.copy(ROOT / "items.db", OUTPUT / "items.db")
    shutil.copy(
        ROOT / "src" / "arc_helper" / "resolutions.json", OUTPUT / "resolutions.json"
    )
    if (ROOT / "README.md").exists():
        shutil.copy(ROOT / "README.md", OUTPUT / "README.md")

    # Bundle Tesseract
    tesseract_dest = OUTPUT / "tesseract"
    if TESSERACT_SRC.exists():
        logger.info("Bundling Tesseract from %s...", TESSERACT_SRC)

        tesseract_dest.mkdir(exist_ok=True)

        for file in TESSERACT_SRC.glob("*.exe"):
            shutil.copy(file, tesseract_dest)
        for file in TESSERACT_SRC.glob("*.dll"):
            shutil.copy(file, tesseract_dest)

        tessdata_dest = tesseract_dest / "tessdata"
        tessdata_dest.mkdir(exist_ok=True)
        tessdata_src = TESSERACT_SRC / "tessdata"

        for filename in ["eng.traineddata", "osd.traineddata"]:
            src = tessdata_src / filename
            if src.exists():
                shutil.copy(src, tessdata_dest)

        logger.info("Tesseract bundled successfully")
    else:
        logger.warning("Tesseract not found at %s", TESSERACT_SRC)

    # Summary
    total_size = sum(f.stat().st_size for f in OUTPUT.rglob("*") if f.is_file())

    logger.info("=" * 50)
    logger.info("Build complete!")
    logger.info("=" * 50)
    logger.info("Output folder: %s", OUTPUT)
    logger.info(f"Total size: {total_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    build()
