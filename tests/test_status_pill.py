"""Status pill repaints the whole surface when the label shrinks."""

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from arc_helper.ui.status import StatusWindow

_MAP_TEAL = (0x2D, 0xD4, 0xBF)
_SCAN_GRAY = (0x88, 0x88, 0x88)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _near(color: QColor, rgb: tuple[int, int, int], tol: int = 36) -> bool:
    return (
        abs(color.red() - rgb[0]) <= tol
        and abs(color.green() - rgb[1]) <= tol
        and abs(color.blue() - rgb[2]) <= tol
    )


def _has_near(window: StatusWindow, rgb: tuple[int, int, int]) -> bool:
    image = window.grab().toImage()
    for y in range(image.height()):
        for x in range(image.width()):
            if _near(image.pixelColor(x, y), rgb):
                return True
    return False


def test_shrink_from_map_drops_teal(qapp):
    _ = qapp
    window = StatusWindow()
    try:
        window.set_map()
        map_width = window.width()
        assert _has_near(window, _MAP_TEAL)

        window.set_scanning()
        assert window.width() < map_width
        assert not _has_near(window, _MAP_TEAL)
        assert _has_near(window, _SCAN_GRAY)
    finally:
        window.hide()
        window.deleteLater()
