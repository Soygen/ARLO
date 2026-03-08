"""
Resolution profiles for ARLO.
Contains pre-calibrated settings for common screen resolutions.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .config import APP_DIR
from .config import SettingsManager
from .config import TooltipCaptureSettings
from .config import TriggerRegion
from .config import TriggerRegion2
from .config import get_screen_resolution
from .config import get_settings
from .config import logger


@dataclass
class ResolutionProfile:
    """Pre-calibrated settings for a specific resolution."""

    trigger_region: dict
    trigger_region2: dict
    tooltip_capture: dict


class ResolutionProfileManager:
    """Manages resolution-based configuration profiles."""

    PROFILES_FILE = "resolutions.json"

    # These values indicate uncalibrated/first-run state
    UNCALIBRATED_MARKER = {"x": 0, "y": 0, "width": 1, "height": 1}

    def __init__(self):
        self.profiles: dict[str, ResolutionProfile] = {}
        self._load_profiles()

    def _get_profiles_path(self) -> Path:
        """Get path to profiles file."""
        # Check in app directory first (for bundled app)
        app_path = APP_DIR / self.PROFILES_FILE
        if app_path.exists():
            return app_path

        # Fall back to package directory (for development)
        package_path = Path(__file__).parent / self.PROFILES_FILE
        if package_path.exists():
            return package_path

        return app_path  # Default to app directory

    def _load_profiles(self) -> None:
        """Load resolution profiles from JSON file."""
        profiles_path = self._get_profiles_path()

        if not profiles_path.exists():
            return

        try:
            with Path(profiles_path).open(encoding="utf-8") as f:
                data = json.load(f)

            for resolution, profile_data in data.get("resolutions", {}).items():
                self.profiles[resolution] = ResolutionProfile(
                    trigger_region=profile_data.get("trigger_region", {}),
                    trigger_region2=profile_data.get("trigger_region2", {}),
                    tooltip_capture=profile_data.get("tooltip_capture", {}),
                )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Warning: Failed to load resolution profiles: {e}")

    def get_resolution_key(self) -> str:
        """Get the current screen resolution as a string key."""
        width, height = get_screen_resolution()
        return f"{width}x{height}"

    def has_profile(self, resolution: str | None = None) -> bool:
        """Check if a profile exists for the given or current resolution."""
        if resolution is None:
            resolution = self.get_resolution_key()

        if resolution not in self.profiles:
            return False

        # Check if the profile has valid (non-null) values
        profile = self.profiles[resolution]
        return profile.trigger_region.get("x") is not None

    def get_profile(self, resolution: str | None = None) -> ResolutionProfile | None:
        """Get profile for the given or current resolution."""
        if resolution is None:
            resolution = self.get_resolution_key()

        return self.profiles.get(resolution)

    def is_uncalibrated(self) -> bool:
        """Check if current settings indicate an uncalibrated state."""
        settings = get_settings()

        # Check if trigger region matches uncalibrated marker
        tr = settings.trigger_region
        return (
            tr.x == self.UNCALIBRATED_MARKER["x"]
            and tr.y == self.UNCALIBRATED_MARKER["y"]
            and tr.width == self.UNCALIBRATED_MARKER["width"]
            and tr.height == self.UNCALIBRATED_MARKER["height"]
        )

    def apply_profile(self, resolution: str | None = None) -> bool:
        """
        Apply profile for the given or current resolution.

        Returns True if profile was applied, False if not found.
        """
        profile = self.get_profile(resolution)

        if profile is None or profile.trigger_region.get("x") is None:
            return False

        # Get current settings and update with profile values
        settings = get_settings()

        # Create new settings with profile values
        new_settings = settings.model_copy(
            update={
                "trigger_region": TriggerRegion(**profile.trigger_region),
                "trigger_region2": TriggerRegion2(**profile.trigger_region2),
                "tooltip_capture": TooltipCaptureSettings(**profile.tooltip_capture),
            }
        )

        # Save to .env file
        new_settings.save_to_env()

        # Reload settings
        SettingsManager.reload()

        return True

    def get_supported_resolutions(self) -> list[str]:
        """Get list of resolutions with complete profiles."""
        return [
            res
            for res, profile in self.profiles.items()
            if profile.trigger_region.get("x") is not None
        ]


# Singleton instance
_profile_manager: ResolutionProfileManager | None = None


class ProfileManagerSingleton:
    """Manages the singleton ResolutionProfileManager instance."""

    _instance: ResolutionProfileManager | None = None

    @classmethod
    def get(cls) -> ResolutionProfileManager:
        """Get the profile manager instance."""
        if cls._instance is None:
            cls._instance = ResolutionProfileManager()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton."""
        cls._instance = None


def get_profile_manager() -> ResolutionProfileManager:
    """Get the singleton profile manager instance."""
    return ProfileManagerSingleton.get()
