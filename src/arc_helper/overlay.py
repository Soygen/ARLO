"""Compatibility re-exports — UI lives in ``arc_helper.ui``."""

from arc_helper.overlay_format import ACTION_COLORS
from arc_helper.overlay_format import BG_DARK
from arc_helper.overlay_format import BG_PANEL
from arc_helper.overlay_format import BORDER_COLOR
from arc_helper.overlay_format import RECYCLE_SYMBOL
from arc_helper.overlay_format import TEXT_DIM
from arc_helper.overlay_format import TEXT_INFO
from arc_helper.overlay_format import format_detail_parts
from arc_helper.overlay_format import format_detail_text
from arc_helper.overlay_format import normalize_keep_labels
from arc_helper.ui.overlay import OverlayWindow
from arc_helper.ui.status import StatusWindow

__all__ = [
    "ACTION_COLORS",
    "BG_DARK",
    "BG_PANEL",
    "BORDER_COLOR",
    "RECYCLE_SYMBOL",
    "TEXT_DIM",
    "TEXT_INFO",
    "OverlayWindow",
    "StatusWindow",
    "format_detail_parts",
    "format_detail_text",
    "normalize_keep_labels",
]
