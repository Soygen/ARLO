"""
Overlay UI for Arc Raiders Helper.
Displays item recommendations as a non-intrusive popup.
"""

# DPI awareness must be set before any GUI operations
import ctypes
import tkinter as tk
from contextlib import suppress

try:
    # Windows 10 1607+ (most reliable)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except (AttributeError, OSError):
    with suppress(AttributeError, OSError):
        # Fallback for older Windows
        ctypes.windll.user32.SetProcessDPIAware()

from .config import get_dpi_scale
from .config import get_settings
from .database import Item

# Action colors for visual feedback
ACTION_COLORS: dict[str, str] = {
    "SELL": "#FFD700",  # Gold
    "RECYCLE": "#00CED1",  # Dark Turquoise
    "KEEP": "#32CD32",  # Lime Green
    "USE": "#FF69B4",  # Hot Pink
    "TRASH": "#FF4444",  # Red
    "UNKNOWN": "#888888",  # Gray
}


class OverlayWindow:
    """Transparent overlay window for showing recommendations."""

    def __init__(self, root: tk.Tk):
        """Initialize overlay as a toplevel window."""
        self.root = root
        self.settings = get_settings()

        # Get DPI scale to counteract Windows scaling
        self.dpi_scale = get_dpi_scale()

        # Create toplevel window for overlay
        self.window = tk.Toplevel(root)
        self.window.title("Arc Raiders Helper")

        # Make window transparent and always on top
        self.window.attributes("-topmost", True)  # noqa: FBT003
        self.window.attributes("-alpha", 0.9)
        self.window.overrideredirect(boolean=True)  # Remove window decorations

        # Start hidden
        self.window.withdraw()

        # Position
        self.overlay_x = self.settings.overlay.x
        self.overlay_y = self.settings.overlay.y

        # Setup UI
        self._setup_ui()

        # Auto-hide timer ID
        self._hide_after_id: str | None = None

    def _scale(self, value: int) -> int:
        """Scale a value down to counteract DPI scaling."""
        return max(1, int(value / self.dpi_scale))

    def _setup_ui(self) -> None:
        """Create the overlay UI elements."""
        # Scale dimensions for padding/borders
        padx = self._scale(15)
        pady = self._scale(10)
        highlight = max(1, self._scale(2))

        # Scale font sizes
        name_font_size = max(8, self._scale(14))
        action_font_size = max(10, self._scale(20))
        notes_font_size = max(6, self._scale(10))

        # Main frame with dark background
        self.frame = tk.Frame(
            self.window,
            bg="#1a1a2e",
            padx=padx,
            pady=pady,
            highlightbackground="#4a4a6a",
            highlightthickness=highlight,
        )
        self.frame.pack()

        # Item name label
        self.name_label = tk.Label(
            self.frame,
            text="Item Name",
            font=("Segoe UI", name_font_size, "bold"),
            fg="white",
            bg="#1a1a2e",
            anchor="w",
        )
        self.name_label.pack(anchor="w")

        # Action label (big and colored)
        self.action_label = tk.Label(
            self.frame,
            text="ACTION",
            font=("Segoe UI", action_font_size, "bold"),
            fg="#FFD700",
            bg="#1a1a2e",
            anchor="w",
        )
        self.action_label.pack(anchor="w", pady=(self._scale(5), 0))

        # Notes label - wraplength will be updated dynamically in show()
        self.notes_label = tk.Label(
            self.frame,
            text="",
            font=("Segoe UI", notes_font_size),
            fg="#aaaaaa",
            bg="#1a1a2e",
            justify=tk.LEFT,
            anchor="w",
        )
        self.notes_label.pack(anchor="w", pady=(self._scale(5), 0))

    def show(self, item_name: str, recommendation: Item | None) -> None:
        """Show the overlay with item info."""
        # Cancel any pending hide
        if self._hide_after_id:
            self.window.after_cancel(self._hide_after_id)

        # Update content
        self.name_label.config(text=item_name)

        if recommendation:
            action_str = recommendation.action
            # Try to get color for known actions, default to white
            color = ACTION_COLORS.get(action_str.upper(), "#FFFFFF")
            self.action_label.config(text=f"→ {action_str}", fg=color)

            # Show recycle_for or keep_for based on action
            detail_text = ""
            action_upper = action_str.upper()
            if "RECYCLE" in action_upper and recommendation.recycle_for:
                detail_text = f"For: {recommendation.recycle_for}"
            elif recommendation.keep_for:
                detail_text = f"For: {recommendation.keep_for}"
            self.notes_label.config(text=detail_text)
        else:
            self.action_label.config(text="→ UNKNOWN", fg=ACTION_COLORS["UNKNOWN"])
            self.notes_label.config(text="Item not in database")

        # Position and show
        self.window.geometry(f"+{self.overlay_x}+{self.overlay_y}")
        self.window.deiconify()
        self.window.lift()

        # Update wraplength to match the width of the action label
        self.window.update_idletasks()  # Force geometry calculation
        action_width = self.action_label.winfo_width()
        name_width = self.name_label.winfo_width()
        max_width = max(action_width, name_width, 200)  # Minimum 200
        self.notes_label.config(wraplength=max_width)

        # Auto-hide after configured time
        hide_ms = int(self.settings.overlay.display_time * 1000)
        self._hide_after_id = self.window.after(hide_ms, self.hide)

    def hide(self) -> None:
        """Hide the overlay."""
        if self._hide_after_id:
            self.window.after_cancel(self._hide_after_id)
            self._hide_after_id = None
        self.window.withdraw()

    def set_position(self, x: int, y: int) -> None:
        """Update overlay position."""
        self.overlay_x = x
        self.overlay_y = y

    def is_visible(self) -> bool:
        """Check if overlay is currently visible."""
        try:
            return self.window.winfo_viewable()
        except tk.TclError:
            return False


class StatusWindow:
    """Small status indicator showing scanner state."""

    def __init__(self, root: tk.Tk):
        """Initialize status window."""
        self.root = root

        # Get DPI scale to counteract Windows scaling
        self.dpi_scale = get_dpi_scale()

        # Create toplevel for status
        self.window = tk.Toplevel(root)
        self.window.title("Arc Helper Status")
        self.window.attributes("-topmost", True)  # noqa: FBT003
        self.window.attributes("-alpha", 0.8)
        self.window.overrideredirect(boolean=True)

        # Position in corner
        self.window.geometry("+10+10")

        # UI
        self._setup_ui()

    def _scale(self, value: int) -> int:
        """Scale a value down to counteract DPI scaling."""
        return max(1, int(value / self.dpi_scale))

    def _setup_ui(self) -> None:
        """Create the status UI elements."""
        padx = self._scale(8)
        pady = self._scale(4)
        font_size = max(6, self._scale(9))

        self.frame = tk.Frame(self.window, bg="#1a1a2e", padx=padx, pady=pady)
        self.frame.pack()

        self.status_label = tk.Label(
            self.frame,
            text="● Scanning...",
            font=("Segoe UI", font_size),
            fg="#888888",
            bg="#1a1a2e",
        )
        self.status_label.pack()

    def set_scanning(self) -> None:
        """Show scanning state."""
        self.status_label.config(text="● Scanning...", fg="#888888")

    def set_active(self) -> None:
        """Show active/inventory detected state."""
        self.status_label.config(text="● INVENTORY", fg="#32CD32")

    def set_error(self, message: str) -> None:
        """Show error state."""
        self.status_label.config(text=f"● {message}", fg="#FF4444")

    def hide(self) -> None:
        """Hide the status window."""
        self.window.withdraw()

    def show(self) -> None:
        """Show the status window."""
        self.window.deiconify()
