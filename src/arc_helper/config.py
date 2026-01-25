"""
Configuration module for Arc Raiders Helper.
Uses Pydantic Settings for type-safe configuration via environment variables.
"""

import ctypes
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from .logging_config import setup_logging


def get_app_dir() -> Path:
    """Get the application directory (handles both dev and bundled modes)."""
    if getattr(sys, "frozen", False):
        # Running as compiled executable
        return Path(sys.executable).parent
    # Running as script - go up from config.py -> arc_helper -> src -> root
    return Path(__file__).parent.parent.parent


APP_DIR = get_app_dir()


def get_screen_resolution() -> tuple[int, int]:
    """Get the primary monitor resolution in physical pixels."""
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()  # This makes us get physical pixels
    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)
    return width, height


def get_dpi_scale() -> float:
    """Get the current DPI scaling factor (e.g., 1.0, 1.5, 2.0, 3.0)."""
    try:
        # Get the DPI for the primary monitor
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()

        # Get DC for the screen
        hdc = user32.GetDC(0)
        gdi32 = ctypes.windll.gdi32

        # LOGPIXELSX = 88, standard DPI is 96
        dpi = gdi32.GetDeviceCaps(hdc, 88)
        user32.ReleaseDC(0, hdc)

        return dpi / 96.0
    except (AttributeError, OSError):
        return 1.0


def scale_for_dpi(value: int) -> int:
    """Scale a logical value to physical pixels."""
    return int(value * get_dpi_scale())


def unscale_from_dpi(value: int) -> int:
    """Convert physical pixels to logical value."""
    scale = get_dpi_scale()
    if scale == 0:
        return value
    return int(value / scale)


def get_tesseract_path() -> str | None:
    """Find Tesseract executable - checks bundled location first."""
    # Check for bundled Tesseract
    bundled = APP_DIR / "tesseract" / "tesseract.exe"
    if bundled.exists():
        return str(bundled)

    # Check common installation paths
    common_paths = [
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    for path in common_paths:
        if path.exists():
            return str(path)

    # Return None - will use system PATH
    return None


class RegionMixin:
    """Mixin that adds bbox property to region classes."""

    x: int
    y: int
    width: int
    height: int

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        """Get bounding box as (left, top, right, bottom)."""
        return (self.x, self.y, self.x + self.width, self.y + self.height)


class TriggerRegion(RegionMixin, BaseSettings):
    """Region where INVENTORY text appears - menu mode."""

    model_config = SettingsConfigDict(
        env_prefix="TRIGGER_REGION_",
        env_file=APP_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    x: int = Field(default=0, description="Left edge of trigger region")
    y: int = Field(default=0, description="Top edge of trigger region")
    width: int = Field(default=1, description="Width of trigger region")
    height: int = Field(default=1, description="Height of trigger region")


class TriggerRegion2(RegionMixin, BaseSettings):
    """Region where INVENTORY text appears - in-raid mode."""

    model_config = SettingsConfigDict(
        env_prefix="TRIGGER_REGION2_",
        env_file=APP_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    x: int = Field(default=0, description="Left edge of trigger region")
    y: int = Field(default=0, description="Top edge of trigger region")
    width: int = Field(default=1, description="Width of trigger region")
    height: int = Field(default=1, description="Height of trigger region")


class TooltipRegion(RegionMixin, BaseSettings):
    """Region where item name appears in tooltip - used for calibration only."""

    model_config = SettingsConfigDict(
        env_prefix="TOOLTIP_REGION_",
        env_file=APP_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    x: int = Field(default=0, description="Left edge of trigger region")
    y: int = Field(default=0, description="Top edge of trigger region")
    width: int = Field(default=1, description="Width of trigger region")
    height: int = Field(default=1, description="Height of trigger region")


class TooltipCaptureSettings(BaseSettings):
    """Settings for cursor-relative tooltip capture."""

    model_config = SettingsConfigDict(
        env_prefix="TOOLTIP_CAPTURE_",
        env_file=APP_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    width: int = Field(default=1, description="Width of capture area around cursor")
    height: int = Field(default=1, description="Height of capture area around cursor")
    offset_x: int = Field(default=0, description="X offset from cursor")
    offset_y: int = Field(default=0, description="Y offset from cursor")


class OverlaySettings(BaseSettings):
    """Settings for the overlay window."""

    model_config = SettingsConfigDict(
        env_prefix="OVERLAY_",
        env_file=APP_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    x: int = Field(default=100, description="Overlay X position")
    y: int = Field(default=100, description="Overlay Y position")
    display_time: float = Field(
        default=4.0, description="How long overlay stays visible"
    )
    cooldown: float = Field(default=2.0, description="Minimum time between same item")


class ScanSettings(BaseSettings):
    """Settings for scanning intervals."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=APP_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    trigger_scan_interval: float = Field(
        default=0.5, description="Seconds between trigger scans"
    )
    tooltip_scan_interval: float = Field(
        default=0.3, description="Seconds between tooltip scans"
    )


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=APP_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Nested settings
    trigger_region: TriggerRegion = Field(default_factory=TriggerRegion)
    trigger_region2: TriggerRegion2 = Field(default_factory=TriggerRegion2)
    tooltip_region: TooltipRegion = Field(default_factory=TooltipRegion)
    tooltip_capture: TooltipCaptureSettings = Field(
        default_factory=TooltipCaptureSettings
    )
    overlay: OverlaySettings = Field(default_factory=OverlaySettings)
    scan: ScanSettings = Field(default_factory=ScanSettings)

    # Tesseract - auto-detect if not specified
    tesseract_path: str | None = Field(default_factory=get_tesseract_path)

    # Database in app directory
    database_path: Path = Field(default_factory=lambda: APP_DIR / "items.db")

    # Debug settings
    debug_mode: bool = Field(default=False, description="Enable debug mode")
    debug_output_dir: Path = Field(default_factory=lambda: APP_DIR / "debug")
    show_capture_area: bool = Field(
        default=False, description="Show red overlay for capture area"
    )

    def save_to_env(self, env_path: Path | None = None) -> None:
        """Save current settings to .env file."""
        if env_path is None:
            env_path = APP_DIR / ".env"

        lines = [
            "# Arc Raiders Helper Configuration",
            "# =================================",
            "",
            "# Trigger region 1 - Menu mode",
            f"TRIGGER_REGION_X={self.trigger_region.x}",
            f"TRIGGER_REGION_Y={self.trigger_region.y}",
            f"TRIGGER_REGION_WIDTH={self.trigger_region.width}",
            f"TRIGGER_REGION_HEIGHT={self.trigger_region.height}",
            "",
            "# Trigger region 2 - In-raid mode",
            f"TRIGGER_REGION2_X={self.trigger_region2.x}",
            f"TRIGGER_REGION2_Y={self.trigger_region2.y}",
            f"TRIGGER_REGION2_WIDTH={self.trigger_region2.width}",
            f"TRIGGER_REGION2_HEIGHT={self.trigger_region2.height}",
            "",
            "# Tooltip capture",
            f"TOOLTIP_CAPTURE_WIDTH={self.tooltip_capture.width}",
            f"TOOLTIP_CAPTURE_HEIGHT={self.tooltip_capture.height}",
            f"TOOLTIP_CAPTURE_OFFSET_X={self.tooltip_capture.offset_x}",
            f"TOOLTIP_CAPTURE_OFFSET_Y={self.tooltip_capture.offset_y}",
            "",
            "# Overlay settings",
            f"OVERLAY_X={self.overlay.x}",
            f"OVERLAY_Y={self.overlay.y}",
            f"OVERLAY_DISPLAY_TIME={self.overlay.display_time}",
            f"OVERLAY_COOLDOWN={self.overlay.cooldown}",
            "",
            "# Scan intervals",
            f"TRIGGER_SCAN_INTERVAL={self.scan.trigger_scan_interval}",
            f"TOOLTIP_SCAN_INTERVAL={self.scan.tooltip_scan_interval}",
            "",
            "# Debug settings",
            f"DEBUG_MODE={str(self.debug_mode).lower()}",
            f"SHOW_CAPTURE_AREA={str(self.show_capture_area).lower()}",
        ]

        with Path(env_path).open("w", encoding="utf-8") as f:
            f.write("\n".join(lines))
            f.flush()  # Add this line

        # Add this line - reload dotenv to pick up new values
        load_dotenv(env_path, override=True)


class SettingsManager:
    """Manages the singleton Settings instance."""

    _instance: Settings | None = None

    @classmethod
    def get(cls) -> Settings:
        if cls._instance is None:
            cls._instance = Settings()
        return cls._instance

    @classmethod
    def reload(cls) -> Settings:
        cls._instance = Settings()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None


def get_settings() -> Settings:
    return SettingsManager.get()


def reload_settings() -> Settings:
    return SettingsManager.reload()


# =============================================================================
# Initialize logging once when this module is imported
# =============================================================================

_initial_settings = Settings()  # Load settings to get debug_mode
logger = setup_logging(APP_DIR, _initial_settings.debug_mode)
