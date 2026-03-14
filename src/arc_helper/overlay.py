"""
Overlay UI for ARLO (Arc Raiders Loot Overlay).
Displays item recommendations as a styled popup with action colors.
"""

# DPI awareness must be set before any GUI operations
import ctypes
import tkinter as tk
from contextlib import suppress

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    with suppress(AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()

from .config import get_dpi_scale
from .config import get_settings
from .database import Item

# Action colors
ACTION_COLORS: dict[str, str] = {
    "SELL": "#FFD700",
    "RECYCLE": "#00CED1",
    "KEEP": "#32CD32",
    "USE": "#FF69B4",
    "TRASH": "#FF4444",
    "UNKNOWN": "#888888",
}

# Background palette
BG_DARK = "#12121f"
BG_PANEL = "#1a1a2e"
BORDER_COLOR = "#3a3a5c"
TEXT_DIM = "#9999bb"
TEXT_INFO = "#c4a0ff"


class OverlayWindow:
    """Transparent overlay window for showing recommendations."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = get_settings()
        self.dpi_scale = get_dpi_scale()

        self.window = tk.Toplevel(root)
        self.window.title("ARLO")
        self.window.attributes("-topmost", True)  # noqa: FBT003
        self.window.attributes("-alpha", 0.92)
        self.window.overrideredirect(boolean=True)
        self.window.withdraw()

        self.overlay_x = self.settings.overlay.x
        self.overlay_y = self.settings.overlay.y

        self._setup_ui()

        self._hide_after_id: str | None = None
        self._current_item: str | None = None

    def _scale(self, value: int) -> int:
        return max(1, int(value / self.dpi_scale))

    def _setup_ui(self) -> None:
        """Build the overlay layout."""
        padx = self._scale(18)
        pady = self._scale(12)
        border_w = max(1, self._scale(2))

        # Font sizes - bigger and bolder
        name_size = max(10, self._scale(18))
        action_size = max(12, self._scale(26))
        info_size = max(8, self._scale(13))
        detail_size = max(8, self._scale(12))

        # Outer frame with border
        self.outer_frame = tk.Frame(
            self.window,
            bg=BORDER_COLOR,
            padx=border_w,
            pady=border_w,
        )
        self.outer_frame.pack()

        # Inner content area
        self.frame = tk.Frame(
            self.outer_frame,
            bg=BG_PANEL,
            padx=padx,
            pady=pady,
        )
        self.frame.pack()

        # Colored accent bar at the top (changes color with action)
        self.accent_bar = tk.Frame(
            self.frame,
            bg="#32CD32",
            height=max(2, self._scale(4)),
        )
        self.accent_bar.pack(fill="x", pady=(0, self._scale(8)))

        # Item name
        self.name_label = tk.Label(
            self.frame,
            text="Item Name",
            font=("Segoe UI", name_size, "bold"),
            fg="white",
            bg=BG_PANEL,
            anchor="w",
        )
        self.name_label.pack(anchor="w")

        # Action label (large, colored)
        self.action_label = tk.Label(
            self.frame,
            text="ACTION",
            font=("Segoe UI", action_size, "bold"),
            fg="#FFD700",
            bg=BG_PANEL,
            anchor="w",
        )
        self.action_label.pack(anchor="w", pady=(self._scale(4), 0))

        # Sell price / stack size info line
        self.info_label = tk.Label(
            self.frame,
            text="",
            font=("Segoe UI", info_size),
            fg=TEXT_INFO,
            bg=BG_PANEL,
            anchor="w",
        )
        self.info_label.pack(anchor="w", pady=(self._scale(4), 0))

        # Thin separator before details
        self.separator = tk.Frame(
            self.frame,
            bg=BORDER_COLOR,
            height=max(1, self._scale(1)),
        )
        # Only packed when there's detail text (see show())

        # Detail / notes label (recycle_for or keep_for)
        self.notes_label = tk.Label(
            self.frame,
            text="",
            font=("Segoe UI", detail_size),
            fg=TEXT_DIM,
            bg=BG_PANEL,
            justify=tk.LEFT,
            anchor="w",
        )
        # Only packed when there's detail text (see show())

    def show(self, item_name: str, recommendation: Item | None) -> None:
        """Show the overlay with item info."""
        # Skip redraw if same item already showing
        if self._current_item == item_name and self.is_visible():
            if self._hide_after_id:
                self.window.after_cancel(self._hide_after_id)
            hide_ms = int(self.settings.overlay.display_time * 1000)
            self._hide_after_id = self.window.after(hide_ms, self.hide)
            return

        self._current_item = item_name

        if self._hide_after_id:
            self.window.after_cancel(self._hide_after_id)

        # Update content
        self.name_label.config(text=item_name)

        if recommendation:
            action_str = recommendation.action
            action_upper = action_str.upper()

            # Pick color: check for keywords in multi-word actions
            color = "#FFFFFF"
            for keyword, c in ACTION_COLORS.items():
                if keyword in action_upper:
                    color = c
                    break

            self.action_label.config(text=f"\u2192 {action_str}", fg=color)
            self.accent_bar.config(bg=color)

            # Info line
            info_parts: list[str] = []
            if recommendation.sell_price is not None:
                info_parts.append(f"Sell: {recommendation.sell_price:,}\u20a1")
            if recommendation.stack_size is not None:
                info_parts.append(f"Stack: {recommendation.stack_size}")
            self.info_label.config(text="    \u00b7    ".join(info_parts))

            # Detail text
            detail_text = ""
            if "RECYCLE" in action_upper and recommendation.recycle_for:
                detail_text = f"Recycles into: {recommendation.recycle_for}"
            elif recommendation.keep_for:
                detail_text = recommendation.keep_for

            # Show or hide separator and notes based on content
            self.separator.pack_forget()
            self.notes_label.pack_forget()
            if detail_text:
                self.separator.pack(fill="x", pady=(self._scale(8), self._scale(6)))
                self.notes_label.pack(anchor="w")
                self.notes_label.config(text=detail_text)
        else:
            self.action_label.config(text="\u2192 UNKNOWN", fg=ACTION_COLORS["UNKNOWN"])
            self.accent_bar.config(bg=ACTION_COLORS["UNKNOWN"])
            self.info_label.config(text="")
            self.separator.pack_forget()
            self.notes_label.pack_forget()
            self.notes_label.pack(anchor="w", pady=(self._scale(4), 0))
            self.notes_label.config(text="Item not in database")

        # Position and show
        self.window.geometry(f"+{self.overlay_x}+{self.overlay_y}")
        self.window.deiconify()
        self.window.lift()

        # Update wraplength
        self.window.update_idletasks()
        widths = [
            self.action_label.winfo_width(),
            self.name_label.winfo_width(),
            self.info_label.winfo_width(),
        ]
        max_width = max(*widths, self._scale(280))
        self.notes_label.config(wraplength=max_width)

        # Auto-hide
        hide_ms = int(self.settings.overlay.display_time * 1000)
        self._hide_after_id = self.window.after(hide_ms, self.hide)

    def hide(self) -> None:
        if self._hide_after_id:
            self.window.after_cancel(self._hide_after_id)
            self._hide_after_id = None
        self._current_item = None
        self.window.withdraw()

    def set_position(self, x: int, y: int) -> None:
        self.overlay_x = x
        self.overlay_y = y

    def is_visible(self) -> bool:
        try:
            return self.window.winfo_viewable()
        except tk.TclError:
            return False


class StatusWindow:
    """Small status indicator showing scanner state."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.dpi_scale = get_dpi_scale()

        self.window = tk.Toplevel(root)
        self.window.title("ARLO Status")
        self.window.attributes("-topmost", True)  # noqa: FBT003
        self.window.attributes("-alpha", 0.8)
        self.window.overrideredirect(boolean=True)
        self.window.geometry("+10+10")

        self._setup_ui()

    def _scale(self, value: int) -> int:
        return max(1, int(value / self.dpi_scale))

    def _setup_ui(self) -> None:
        padx = self._scale(10)
        pady = self._scale(5)
        font_size = max(7, self._scale(10))

        self.frame = tk.Frame(self.window, bg=BG_DARK, padx=padx, pady=pady)
        self.frame.pack()

        self.status_label = tk.Label(
            self.frame,
            text="\u25cf Scanning...",
            font=("Segoe UI", font_size),
            fg="#888888",
            bg=BG_DARK,
        )
        self.status_label.pack()

    def set_scanning(self) -> None:
        self.status_label.config(text="\u25cf Scanning...", fg="#888888")

    def set_active(self) -> None:
        self.status_label.config(text="\u25cf INVENTORY", fg="#32CD32")

    def set_hotkey(self) -> None:
        self.status_label.config(text="\u25cf HOTKEY SCAN", fg="#FFD700")

    def set_error(self, message: str) -> None:
        self.status_label.config(text=f"\u25cf {message}", fg="#FF4444")

    def hide(self) -> None:
        self.window.withdraw()

    def show(self) -> None:
        self.window.deiconify()
