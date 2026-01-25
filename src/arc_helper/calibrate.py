"""
Calibration tool for Arc Raiders Helper.
Helps configure screen regions for trigger and tooltip detection.
"""

# Enable windows DPI scaling
import ctypes
import tkinter as tk
from contextlib import suppress
from pathlib import Path
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk

import pytesseract
from PIL import ImageGrab
from PIL import ImageTk

from arc_helper.config import APP_DIR
from arc_helper.config import OverlaySettings
from arc_helper.config import ScanSettings
from arc_helper.config import Settings
from arc_helper.config import TooltipCaptureSettings
from arc_helper.config import TooltipRegion
from arc_helper.config import TriggerRegion
from arc_helper.config import TriggerRegion2
from arc_helper.config import get_screen_resolution
from arc_helper.config import get_settings
from arc_helper.config import logger
from arc_helper.database import get_database
from arc_helper.ocr import get_ocr_engine

try:
    # Windows 10 1607+ (most reliable)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except (AttributeError, OSError):
    with suppress(AttributeError, OSError):
        # Fallback for older Windows
        ctypes.windll.user32.SetProcessDPIAware()


class RegionSelector:
    """Widget for configuring a screen region."""

    def __init__(
        self,
        parent: ttk.Frame,
        title: str,
        initial_x: int,
        initial_y: int,
        initial_width: int,
        initial_height: int,
        color: str = "red",
    ):
        self.parent = parent
        self.title = title
        self.color = color

        # Current values
        self.x = tk.IntVar(value=initial_x)
        self.y = tk.IntVar(value=initial_y)
        self.width = tk.IntVar(value=initial_width)
        self.height = tk.IntVar(value=initial_height)

        # Overlay window for visualization
        self.overlay: tk.Toplevel | None = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create UI elements."""
        # Frame for this region
        frame = ttk.LabelFrame(self.parent, text=self.title, padding=10)
        frame.pack(fill="x", pady=5)

        # Grid of sliders
        sliders = [
            ("X", self.x, 0, 3000),
            ("Y", self.y, 0, 2000),
            ("Width", self.width, 20, 800),
            ("Height", self.height, 10, 300),
        ]

        for row, (label, var, min_val, max_val) in enumerate(sliders):
            ttk.Label(frame, text=label, width=6).grid(row=row, column=0, sticky="w")

            slider = ttk.Scale(
                frame,
                from_=min_val,
                to=max_val,
                variable=var,
                orient="horizontal",
                length=200,
                command=lambda _: self._on_change(),
            )
            slider.grid(row=row, column=1, sticky="ew", padx=5)

            value_label = ttk.Label(frame, textvariable=var, width=5)
            value_label.grid(row=row, column=2)

        frame.columnconfigure(1, weight=1)

    def _on_change(self) -> None:
        """Update overlay when values change."""
        if self.overlay and self.overlay.winfo_exists():
            self._update_overlay()

    def show_overlay(self) -> None:
        """Show colored rectangle on screen."""
        if self.overlay:
            self.overlay.destroy()

        self.overlay = tk.Toplevel()
        self.overlay.attributes("-alpha", 0.4)
        self.overlay.overrideredirect(boolean=True)
        self.overlay.config(bg=self.color)

        self._update_overlay()

    def _update_overlay(self) -> None:
        """Update overlay position and size."""
        if self.overlay and self.overlay.winfo_exists():
            self.overlay.geometry(
                f"{self.width.get()}x{self.height.get()}+{self.x.get()}+{self.y.get()}"
            )

    def hide_overlay(self) -> None:
        """Hide the overlay."""
        if self.overlay:
            try:
                if self.overlay.winfo_exists():
                    self.overlay.destroy()
            except tk.TclError:
                pass  # Window already destroyed
            self.overlay = None

    def get_bbox(self) -> tuple[int, int, int, int]:
        """Get region as (left, top, right, bottom)."""
        x, y = self.x.get(), self.y.get()
        return (x, y, x + self.width.get(), y + self.height.get())


class TooltipCaptureConfig:
    """Widget for configuring cursor-relative tooltip capture."""

    def __init__(
        self,
        parent: ttk.Frame,
        initial_width: int,
        initial_height: int,
        initial_offset_x: int,
        initial_offset_y: int,
    ):
        self.parent = parent

        # Current values
        self.width = tk.IntVar(value=initial_width)
        self.height = tk.IntVar(value=initial_height)
        self.offset_x = tk.IntVar(value=initial_offset_x)
        self.offset_y = tk.IntVar(value=initial_offset_y)

        # Overlay window for visualization
        self.overlay: tk.Toplevel | None = None
        self.is_tracking = False

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create UI elements."""
        frame = ttk.LabelFrame(
            self.parent, text="Tooltip Capture (follows cursor)", padding=10
        )
        frame.pack(fill="x", pady=5)

        # Explanation
        ttk.Label(
            frame,
            text="This area follows your cursor. Adjust offset and size\nso it captures the tooltip when hovering over items.",
            justify="left",
            foreground="gray",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        # Grid of sliders
        sliders = [
            ("Width", self.width, 100, 1200),
            ("Height", self.height, 100, 1200),
            ("Offset X", self.offset_x, -1200, 800),
            ("Offset Y", self.offset_y, -1200, -100),
        ]

        for row, (label, var, min_val, max_val) in enumerate(sliders, start=1):
            ttk.Label(frame, text=label, width=8).grid(row=row, column=0, sticky="w")

            slider = ttk.Scale(
                frame,
                from_=min_val,
                to=max_val,
                variable=var,
                orient="horizontal",
                length=200,
                command=lambda _: self._on_change(),
            )
            slider.grid(row=row, column=1, sticky="ew", padx=5)

            value_label = ttk.Label(frame, textvariable=var, width=6)
            value_label.grid(row=row, column=2)

        frame.columnconfigure(1, weight=1)

    def _on_change(self) -> None:
        """Update overlay when values change."""
        if self.overlay and self.overlay.winfo_exists():
            self._update_overlay_position()

    def start_tracking(self) -> None:
        """Start showing overlay that follows cursor."""
        if self.overlay:
            self.stop_tracking()

        self.overlay = tk.Toplevel()
        self.overlay.attributes("-topmost", True)  # noqa: FBT003
        self.overlay.attributes("-alpha", 0.3)
        self.overlay.overrideredirect(boolean=True)
        self.overlay.config(bg="green")

        self.is_tracking = True
        self._track_cursor()

    def _track_cursor(self) -> None:
        """Update overlay position to follow cursor."""
        if not self.is_tracking or not self.overlay:
            return

        try:
            if not self.overlay.winfo_exists():
                self.is_tracking = False
                return

            self._update_overlay_position()

            # Schedule next update (50ms = 20fps)
            self.overlay.after(50, self._track_cursor)

        except tk.TclError:
            self.is_tracking = False

    def _update_overlay_position(self) -> None:
        """Update overlay to current cursor position + offset."""
        if not self.overlay or not self.overlay.winfo_exists():
            return

        try:
            x = self.overlay.winfo_pointerx()
            y = self.overlay.winfo_pointery()
        except tk.TclError:
            return

        # TODO: uncomment the following once correct percentages
        # for screen thresholds have been found and implemented.
        # screen_width, _ = get_screen_resolution()

        # Check if cursor is in right 30% of screen
        # right_threshold = screen_width * 0.7

        # if x > right_threshold:
        #     # Flip X offset (and account for capture width)
        #     offset_x = -self.offset_x.get() - self.width.get()
        # else:
        #     offset_x = self.offset_x.get()
        offset_x = self.offset_x.get()

        left = x + offset_x
        top = y + self.offset_y.get()

        self.overlay.geometry(f"{self.width.get()}x{self.height.get()}+{left}+{top}")

    def stop_tracking(self) -> None:
        """Stop tracking and hide overlay."""
        self.is_tracking = False
        if self.overlay:
            try:
                if self.overlay.winfo_exists():
                    self.overlay.destroy()
            except tk.TclError:
                pass
            self.overlay = None

    def capture_at_cursor(self) -> tuple[ImageGrab.Image, int, int] | None:
        """Capture the area at current cursor position."""

        try:
            root = self.parent.winfo_toplevel()
            cursor_x = root.winfo_pointerx()
            cursor_y = root.winfo_pointery()
        except tk.TclError:
            return None

        _screen_width, _ = get_screen_resolution()

        # TODO: tooltip flipping on right side of screen, to be implemented properly
        # Check if cursor is in right 30% of screen
        # right_threshold = screen_width * 0.7

        # if cursor_x > right_threshold:
        #     # Flip X offset (and account for capture width)
        #     offset_x = -self.offset_x.get() - self.width.get()
        # else:
        #     offset_x = self.offset_x.get()
        offset_x = self.offset_x.get()

        # Calculate capture region
        left = max(0, cursor_x + offset_x)
        top = max(0, cursor_y + self.offset_y.get())
        right = left + self.width.get()
        bottom = top + self.height.get()

        image = ImageGrab.grab(bbox=(left, top, right, bottom))
        return image, cursor_x, cursor_y


class TempRegion:
    """Temporary region class for OCR testing."""

    def __init__(self, x: int, y: int, w: int, h: int):
        self.x = x
        self.y = y
        self.width = w
        self.height = h

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.width, self.y + self.height)


class CalibrationTool:
    """Main calibration application."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Arc Raiders Helper - Calibration")
        self.root.attributes("-topmost", True)  # noqa: FBT003
        self.root.geometry("600x800")

        # Load current settings
        self.settings = get_settings()

        # OCR engine for testing
        self.ocr = get_ocr_engine()

        # Database reference
        self.db = get_database()

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create the calibration UI."""
        # Create canvas with scrollbar for long content
        canvas = tk.Canvas(self.root)
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Enable mousewheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Main content frame
        main_frame = ttk.Frame(scrollable_frame, padding=15)
        main_frame.pack(fill="both", expand=True)

        # Instructions
        instructions = ttk.Label(
            main_frame,
            text=(
                "Configure the screen regions for Arc Raiders Helper:\n\n"
                "1. TRIGGER regions: Where 'INVENTORY' text appears\n"
                "2. TOOLTIP region: Where item names appear\n\n"
                "Use 'Show' to visualize each region on screen."
            ),
            justify="left",
        )
        instructions.pack(fill="x", pady=(0, 15))

        # =====================================================================
        # Trigger Region 1 (IN-MENU)
        # =====================================================================
        self.trigger_selector = RegionSelector(
            main_frame,
            "Trigger Region 1 (INVENTORY text IN-MENU)",
            self.settings.trigger_region.x,
            self.settings.trigger_region.y,
            self.settings.trigger_region.width,
            self.settings.trigger_region.height,
            color="yellow",
        )

        trigger_btn_frame = ttk.Frame(main_frame)
        trigger_btn_frame.pack(fill="x", pady=5)
        ttk.Button(
            trigger_btn_frame,
            text="Show Region",
            command=self.trigger_selector.show_overlay,
        ).pack(side="left", padx=2)
        ttk.Button(
            trigger_btn_frame, text="Hide", command=self.trigger_selector.hide_overlay
        ).pack(side="left", padx=2)
        ttk.Button(
            trigger_btn_frame, text="Test OCR", command=self._test_trigger1
        ).pack(side="left", padx=2)

        ttk.Separator(main_frame, orient="horizontal").pack(fill="x", pady=10)

        # =====================================================================
        # Trigger Region 2 (IN-GAME)
        # =====================================================================
        self.trigger_selector2 = RegionSelector(
            main_frame,
            "Trigger Region 2 (INVENTORY text IN-GAME)",
            self.settings.trigger_region2.x,
            self.settings.trigger_region2.y,
            self.settings.trigger_region2.width,
            self.settings.trigger_region2.height,
            color="blue",
        )

        trigger_btn_frame2 = ttk.Frame(main_frame)
        trigger_btn_frame2.pack(fill="x", pady=5)
        ttk.Button(
            trigger_btn_frame2,
            text="Show Region",
            command=self.trigger_selector2.show_overlay,
        ).pack(side="left", padx=2)
        ttk.Button(
            trigger_btn_frame2, text="Hide", command=self.trigger_selector2.hide_overlay
        ).pack(side="left", padx=2)
        ttk.Button(
            trigger_btn_frame2, text="Test OCR", command=self._test_trigger2
        ).pack(side="left", padx=2)

        ttk.Separator(main_frame, orient="horizontal").pack(fill="x", pady=10)

        # =====================================================================
        # Tooltip Region
        # =====================================================================
        self.tooltip_capture = TooltipCaptureConfig(
            main_frame,
            initial_width=self.settings.tooltip_capture.width,
            initial_height=self.settings.tooltip_capture.height,
            initial_offset_x=self.settings.tooltip_capture.offset_x,
            initial_offset_y=self.settings.tooltip_capture.offset_y,
        )

        tooltip_btn_frame = ttk.Frame(main_frame)
        tooltip_btn_frame.pack(fill="x", pady=5)
        ttk.Button(
            tooltip_btn_frame,
            text="Start Tracking",
            command=self.tooltip_capture.start_tracking,
        ).pack(side="left", padx=2)
        ttk.Button(
            tooltip_btn_frame,
            text="Stop Tracking",
            command=self.tooltip_capture.stop_tracking,
        ).pack(side="left", padx=2)
        ttk.Button(
            tooltip_btn_frame,
            text="Test OCR at Cursor",
            command=self._test_tooltip_at_cursor,
        ).pack(side="left", padx=2)

        ttk.Separator(main_frame, orient="horizontal").pack(fill="x", pady=10)

        # =====================================================================
        # OCR Preview Area
        # =====================================================================
        preview_frame = ttk.LabelFrame(main_frame, text="OCR Test Result", padding=10)
        preview_frame.pack(fill="x", pady=5)

        self.preview_label = ttk.Label(
            preview_frame, text="Click 'Test OCR' to preview"
        )
        self.preview_label.pack()

        self.result_label = ttk.Label(
            preview_frame, text="", font=("Segoe UI", 11, "bold")
        )
        self.result_label.pack(pady=5)

        ttk.Separator(main_frame, orient="horizontal").pack(fill="x", pady=10)

        # =====================================================================
        # Database Management
        # =====================================================================
        db_frame = ttk.LabelFrame(main_frame, text="Item Database", padding=10)
        db_frame.pack(fill="x", pady=5)

        self.item_count_var = tk.StringVar()
        self._update_item_count()

        ttk.Label(db_frame, textvariable=self.item_count_var).pack(side="left", padx=5)

        ttk.Button(db_frame, text="Load CSV...", command=self._load_csv).pack(
            side="left", padx=5
        )

        ttk.Button(db_frame, text="View Items", command=self._view_items).pack(
            side="left", padx=5
        )

        ttk.Button(db_frame, text="Clear Database", command=self._clear_database).pack(
            side="left", padx=5
        )

        ttk.Separator(main_frame, orient="horizontal").pack(fill="x", pady=10)

        # =====================================================================
        # Action Buttons
        # =====================================================================
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill="x", pady=(15, 0))

        ttk.Button(
            btn_frame, text="Save Configuration", command=self._save_config
        ).pack(side="left", padx=5)

        ttk.Button(
            btn_frame, text="Reset to Defaults", command=self._reset_defaults
        ).pack(side="left", padx=5)

        ttk.Button(btn_frame, text="Close", command=self._on_close).pack(
            side="right", padx=5
        )

    # =========================================================================
    # OCR Testing Methods
    # =========================================================================

    def _test_trigger1(self) -> None:
        """Test OCR on trigger region 1."""
        self._test_region_for_inventory(self.trigger_selector)

    def _test_trigger2(self) -> None:
        """Test OCR on trigger region 2."""
        self._test_region_for_inventory(self.trigger_selector2)

    def _test_region_for_inventory(self, selector: RegionSelector) -> None:
        """Test a region for INVENTORY text."""
        bbox = selector.get_bbox()
        logger.info(f"Testing bbox: {bbox}")  # Debug

        image = ImageGrab.grab(bbox=bbox)
        logger.info(f"Captured image: {image.size}, mode: {image.mode}")  # Debug

        self._show_preview(image)

        region = TempRegion(
            selector.x.get(),
            selector.y.get(),
            selector.width.get(),
            selector.height.get(),
        )
        logger.info(
            f"Region: x={region.x}, y={region.y}, w={region.width}, h={region.height}"
        )  # Debug

        found = self.ocr.check_trigger(region)
        logger.info(f"Trigger found: {found}")  # Debug

        if found:
            self.result_label.config(text="✓ INVENTORY detected!", foreground="green")
        else:
            self.result_label.config(text="✗ INVENTORY not found", foreground="red")

    def _test_tooltip(self) -> None:
        """Test OCR on tooltip region."""
        bbox = self.tooltip_capture.get_bbox()
        image = ImageGrab.grab(bbox=bbox)
        self._show_preview(image)

        region = TempRegion(
            self.tooltip_capture.offset_x.get(),
            self.tooltip_capture.offset_y.get(),
            self.tooltip_capture.width.get(),
            self.tooltip_capture.height.get(),
        )

        item_name = self.ocr.extract_item_name(region)

        if item_name:
            self.result_label.config(text=f"✓ Found: '{item_name}'", foreground="green")
        else:
            self.result_label.config(text="✗ No text detected", foreground="red")

    def _show_preview(self, image) -> None:
        """Show image preview."""

        if image.mode != "RGB":
            image = image.convert("RGB")

        display_width = min(350, image.width)
        ratio = display_width / image.width
        display_height = int(image.height * ratio)
        display_img = image.resize((display_width, display_height))

        photo = ImageTk.PhotoImage(display_img)
        self.preview_label.config(image=photo)
        self.preview_label.image = photo  # Keep reference

    def _test_tooltip_at_cursor(self) -> None:
        """Test OCR on tooltip at current cursor position."""
        logger.info("Testing tooltip at cursor...")  # Debug

        result = self.tooltip_capture.capture_at_cursor()

        if result is None:
            logger.info("Failed to capture")  # Debug
            self.result_label.config(text="✗ Failed to capture", foreground="red")
            self.root.update()
            return

        image, cursor_x, cursor_y = result
        logger.info(
            f"Captured at cursor ({cursor_x}, {cursor_y}), image size: {image.size}"
        )  # Debug

        self._show_preview(image)

        # Use the OCR engine's tooltip preprocessing
        processed = self.ocr.preprocess_tooltip(image)
        logger.info(f"Preprocessed image size: {processed.size}")  # Debug

        try:
            text = pytesseract.image_to_string(processed, config="--psm 6")
            logger.info(f"Raw OCR text: {text!r}")  # Debug

            item_name = self.ocr.parse_item_name_from_tooltip(text)
            logger.info(f"Parsed item name: {item_name}")  # Debug

            if item_name:
                self.result_label.config(
                    text=f"✓ Found: '{item_name}'", foreground="green"
                )
            else:
                self.result_label.config(
                    text="✗ No item name detected", foreground="red"
                )
        except Exception as e:  # noqa: BLE001
            logger.info(f"OCR Error: {e}")  # Debug
            self.result_label.config(text=f"✗ OCR Error: {e}", foreground="red")

        self.root.update()  # Force UI update

    # =========================================================================
    # Configuration Methods
    # =========================================================================

    def _save_config(self) -> None:
        """Save configuration to .env file."""
        settings = Settings(
            trigger_region=TriggerRegion(
                x=self.trigger_selector.x.get(),
                y=self.trigger_selector.y.get(),
                width=self.trigger_selector.width.get(),
                height=self.trigger_selector.height.get(),
            ),
            trigger_region2=TriggerRegion2(
                x=self.trigger_selector2.x.get(),
                y=self.trigger_selector2.y.get(),
                width=self.trigger_selector2.width.get(),
                height=self.trigger_selector2.height.get(),
            ),
            tooltip_region=TooltipRegion(
                x=0,  # No longer used, but keep for compatibility
                y=0,
                width=100,
                height=100,
            ),
            tooltip_capture=TooltipCaptureSettings(
                width=self.tooltip_capture.width.get(),
                height=self.tooltip_capture.height.get(),
                offset_x=self.tooltip_capture.offset_x.get(),
                offset_y=self.tooltip_capture.offset_y.get(),
            ),
            overlay=OverlaySettings(),
            scan=ScanSettings(),
        )

        settings.save_to_env()
        messagebox.showinfo("Saved", "Configuration saved to .env file!")

    def _reset_defaults(self) -> None:
        """Reset to default values."""
        # Trigger 1 defaults
        self.trigger_selector.x.set(0)
        self.trigger_selector.y.set(0)
        self.trigger_selector.width.set(1)
        self.trigger_selector.height.set(1)

        # Trigger 2 defaults
        self.trigger_selector2.x.set(0)
        self.trigger_selector2.y.set(0)
        self.trigger_selector2.width.set(1)
        self.trigger_selector2.height.set(1)

        # Tooltip defaults
        self.tooltip_capture.offset_x.set(0)
        self.tooltip_capture.offset_y.set(0)
        self.tooltip_capture.width.set(1)
        self.tooltip_capture.height.set(1)

    # =========================================================================
    # Database Management Methods
    # =========================================================================

    def _update_item_count(self) -> None:
        """Update the item count display."""
        count = self.db.count()
        self.item_count_var.set(f"Items in database: {count}")

    def _load_csv(self) -> None:
        """Open file picker and load CSV into database."""
        filepath = filedialog.askopenfilename(
            title="Select Items CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialdir=APP_DIR,
        )

        if not filepath:
            return  # User cancelled

        try:
            self.db.load_csv(filepath)
            self._update_item_count()
            messagebox.showinfo(
                "Success",
                f"Loaded items from {Path(filepath).name}\n\n"
                f"Database now contains {self.db.count()} items.",
            )
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Error", f"Failed to load CSV:\n\n{e}")

    def _view_items(self) -> None:
        """Show a window with all items in the database."""
        items = self.db.get_all_items()

        if not items:
            messagebox.showinfo(
                "Database Empty",
                "No items in database.\n\nLoad a CSV file to add items.",
            )
            return

        # Create a new window
        view_window = tk.Toplevel(self.root)
        view_window.title("Item Database")
        view_window.geometry("700x400")
        view_window.attributes("-topmost", True)  # noqa: FBT003

        # Create treeview with scrollbar
        tree_frame = ttk.Frame(view_window)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=10)

        v_scrollbar = ttk.Scrollbar(tree_frame, orient="vertical")
        v_scrollbar.pack(side="right", fill="y")

        h_scrollbar = ttk.Scrollbar(tree_frame, orient="horizontal")
        h_scrollbar.pack(side="bottom", fill="x")

        tree = ttk.Treeview(
            tree_frame,
            columns=("name", "action", "recycle_for", "keep_for"),
            show="headings",
            yscrollcommand=v_scrollbar.set,
            xscrollcommand=h_scrollbar.set,
        )
        tree.pack(fill="both", expand=True)

        v_scrollbar.config(command=tree.yview)
        h_scrollbar.config(command=tree.xview)

        # Configure columns
        tree.heading("name", text="Item Name")
        tree.heading("action", text="Action")
        tree.heading("recycle_for", text="Recycle For")
        tree.heading("keep_for", text="Keep For")

        tree.column("name", width=200, minwidth=100)
        tree.column("action", width=80, minwidth=60)
        tree.column("recycle_for", width=180, minwidth=100)
        tree.column("keep_for", width=180, minwidth=100)

        # Add items
        for item in items:
            tree.insert(
                "",
                "end",
                values=(
                    item.name,
                    item.action,
                    item.recycle_for or "",
                    item.keep_for or "",
                ),
            )

        # Bottom frame
        bottom_frame = ttk.Frame(view_window)
        bottom_frame.pack(fill="x", padx=10, pady=10)

        ttk.Label(bottom_frame, text=f"Total items: {len(items)}").pack(side="left")

        ttk.Button(bottom_frame, text="Close", command=view_window.destroy).pack(
            side="right"
        )

    def _clear_database(self) -> None:
        """Clear all items from the database."""
        count = self.db.count()

        if count == 0:
            messagebox.showinfo("Database Empty", "Database is already empty.")
            return

        confirm = messagebox.askyesno(
            "Confirm Clear",
            f"Are you sure you want to delete all {count} items?\n\n"
            "This cannot be undone.",
        )

        if confirm:
            self.db.clear()
            self._update_item_count()
            messagebox.showinfo("Cleared", "Database cleared successfully.")

    # =========================================================================
    # Window Management
    # =========================================================================

    def run(self) -> None:
        """Start the calibration tool."""
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self) -> None:
        """Handle window close."""
        self.trigger_selector.hide_overlay()
        self.trigger_selector2.hide_overlay()
        self.tooltip_capture.stop_tracking()  # Changed from tooltip_selector
        self.root.destroy()


def main() -> None:
    """Entry point for calibration tool."""
    tool = CalibrationTool()
    tool.run()


if __name__ == "__main__":
    main()
