"""Shared pytest fixtures for Arc Raiders Helper tests."""

from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Provide a temporary database path."""
    return tmp_path / "test_items.db"


@pytest.fixture
def temp_env_path(tmp_path: Path) -> Path:
    """Provide a temporary .env file path."""
    return tmp_path / ".env"


@pytest.fixture
def mock_settings():
    """Mock settings with default values."""
    with patch("arc_helper.config.SettingsManager") as mock_mgr:
        mock_instance = MagicMock()
        mock_instance.stations.gear_bench = 0
        mock_instance.stations.gunsmith = 0
        mock_instance.stations.medical_lab = 0
        mock_instance.stations.explosives_station = 0
        mock_instance.stations.utility_station = 0
        mock_instance.stations.refiner = 0
        mock_instance.stations.scrappy = 0
        mock_mgr.get.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def sample_csv_content() -> str:
    """Sample CSV content for testing."""
    return """name,action,recycle_for,keep_for,sell_price
Test Item,Keep,2x Metal Parts,5x for Gear Bench III,1000
Sell Item,Sell,,,500
Recycle Item,Recycle,4x Plastic Parts,,750
"""


@pytest.fixture
def sample_csv_file(tmp_path: Path, sample_csv_content: str) -> Path:
    """Create a sample CSV file for testing."""
    csv_path = tmp_path / "test_items.csv"
    csv_path.write_text(sample_csv_content, encoding="utf-8")
    return csv_path
