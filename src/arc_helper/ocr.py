"""
OCR module for Arc Raiders Helper.
Screen capture and text extraction using Tesseract.
"""

# Enable windows DPI scaling
import ctypes
import re
import string
import typing
from contextlib import suppress

import numpy as np
import pytesseract
from PIL import Image
from PIL import ImageGrab
from PIL import ImageOps
from pydantic import BaseModel

from .config import RegionMixin
from .config import get_screen_resolution
from .config import get_settings
from .config import logger

try:
    # Windows 10 1607+ (most reliable)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except (AttributeError, OSError):
    with suppress(AttributeError, OSError):
        # Fallback for older Windows
        ctypes.windll.user32.SetProcessDPIAware()


class Point(BaseModel):
    """Screen coordinates."""

    x: int
    y: int


class OCRResult(BaseModel):
    """Result of an OCR operation."""

    text: str | None
    confidence: float = 0.0
    raw_text: str = ""


def get_cursor_position() -> Point:
    """Get current cursor position on screen in physical pixels."""

    # Ensure we're DPI aware to get physical coordinates
    ctypes.windll.user32.SetProcessDPIAware()

    class POINT(ctypes.Structure):
        _fields_: typing.ClassVar = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return Point(x=pt.x, y=pt.y)


class OCREngine:
    """OCR engine for extracting text from screen regions."""

    # The trigger word we're looking for
    TRIGGER_WORD = "INVENTORY"

    def __init__(self):
        """Initialize OCR engine."""
        settings = get_settings()

        # Set Tesseract path if configured
        if settings.tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = settings.tesseract_path

        self.debug_mode = settings.debug_mode
        self.debug_dir = settings.debug_output_dir

        # Tooltip capture settings
        self.tooltip_width = settings.tooltip_capture.width
        self.tooltip_height = settings.tooltip_capture.height
        self.tooltip_offset_x = settings.tooltip_capture.offset_x
        self.tooltip_offset_y = settings.tooltip_capture.offset_y

        if self.debug_mode:
            self.debug_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def capture_region(region: RegionMixin) -> Image.Image:
        """Capture a screen region."""
        try:
            return ImageGrab.grab(bbox=region.bbox)
        except OSError as e:
            logger.error(f"Screen grab failed for region {region.bbox}: {e}")
            # Return a dummy image to avoid crashing
            return Image.new("RGB", (region.width, region.height), color="black")

    def capture_around_cursor(self) -> tuple[Image.Image, Point]:
        """
        Capture a region around the current cursor position.
        Returns the captured image and the cursor position.

        If cursor is in the right 30% of the screen, the X offset is flipped
        to capture the tooltip on the left side instead.
        """

        cursor = get_cursor_position()
        screen_width, screen_height = get_screen_resolution()

        # TODO: flipping the tooltip region on the right side of screen
        # to be implemented properly
        # # Check if cursor is in right 30% of screen
        # right_threshold = screen_width * 0.7

        # if cursor.x > right_threshold:
        #     # Flip X offset (and account for capture width)
        #     offset_x = -self.tooltip_offset_x - self.tooltip_width
        # else:
        #     offset_x = self.tooltip_offset_x
        offset_x = self.tooltip_offset_x

        # Calculate capture region around cursor
        left = cursor.x + offset_x
        top = cursor.y + self.tooltip_offset_y
        right = left + self.tooltip_width
        bottom = top + self.tooltip_height

        # Clamp to valid screen coordinates (handle multi-monitor)
        # Note: We clamp to primary monitor bounds for simplicity
        left = max(0, min(left, screen_width - 1))
        top = max(0, min(top, screen_height - 1))
        right = max(left + 1, min(right, screen_width))
        bottom = max(top + 1, min(bottom, screen_height))

        # Log if we had to clamp significantly
        if (
            right - left < self.tooltip_width // 2
            or bottom - top < self.tooltip_height // 2
        ):
            logger.debug(
                f"Capture region clamped significantly: cursor=({cursor.x}, {cursor.y}), bbox=({left}, {top}, {right}, {bottom})"
            )

        try:
            image = ImageGrab.grab(bbox=(left, top, right, bottom))
        except OSError as e:
            logger.error(
                f"Screen grab failed at bbox ({left}, {top}, {right}, {bottom}): {e}"
            )
            # Return a dummy image to avoid crashing
            return Image.new("RGB", (100, 100), color="black"), cursor

        return image, cursor

    def check_trigger_any(self, regions: list[RegionMixin]) -> bool:
        """
        Check if the trigger word (INVENTORY) is visible in any of the regions.
        """
        return any(self.check_trigger(region) for region in regions)

    @staticmethod
    def preprocess_for_ocr(
        image: Image.Image, *, invert: bool = True, scale: int = 2
    ) -> Image.Image:
        """
        Preprocess image for better OCR accuracy.

        Args:
            image: Input image
            invert: Whether to invert colors (for white text on dark bg)
            scale: Scale factor for upscaling
        """
        # Convert to grayscale
        gray = image.convert("L")

        # Upscale for better OCR
        if scale > 1:
            new_size = (gray.width * scale, gray.height * scale)
            gray = gray.resize(new_size, Image.Resampling.BILINEAR)

        # Invert if needed (OCR prefers black text on white)
        if invert:
            gray = ImageOps.invert(gray)

        # Apply threshold for cleaner text
        threshold = 128
        gray = gray.point(lambda x: 255 if x > threshold else 0, "1")

        return gray

    def preprocess_tooltip(self, image: Image.Image) -> Image.Image:
        """
        Preprocess tooltip image by isolating the cream-colored tooltip background
        and extracting dark text from it.
        """
        img_array = np.array(image)

        # Tooltip background is #f9eedf = RGB(249, 238, 223)
        lower_bound = np.array([240, 225, 210])
        upper_bound = np.array([255, 250, 240])

        # Create mask for tooltip background
        mask = np.all((img_array >= lower_bound) & (img_array <= upper_bound), axis=2)

        # Find rows and columns that are predominantly cream (>50%)
        row_cream_percentage = np.sum(mask, axis=1) / mask.shape[1]
        col_cream_percentage = np.sum(mask, axis=0) / mask.shape[0]

        # Find first/last row with significant cream
        cream_rows = np.where(row_cream_percentage > 0.5)[0]
        cream_cols = np.where(col_cream_percentage > 0.3)[0]

        if len(cream_rows) == 0 or len(cream_cols) == 0:
            result = Image.new("L", (image.width * 2, image.height * 2), 255)
            return result

        y_min, y_max = cream_rows[0], cream_rows[-1]
        x_min, x_max = cream_cols[0], cream_cols[-1]

        # First crop - to the cream region
        cropped = img_array[y_min:y_max, x_min:x_max]
        cropped_mask = mask[y_min:y_max, x_min:x_max]

        # Find columns that are mostly cream
        col_cream_pct = np.sum(cropped_mask, axis=0) / cropped_mask.shape[0]
        tight_cols = np.where(col_cream_pct > 0.5)[0]

        if len(tight_cols) == 0:
            result = Image.new("L", (image.width * 2, image.height * 2), 255)
            return result

        tight_x_min, tight_x_max = tight_cols[0], tight_cols[-1]

        # Tight crop - removes the colored borders
        tight_cropped = cropped[:, tight_x_min:tight_x_max]

        # Detect "colored" pixels - specifically looking for saturated colors (tags)
        # Tags are colorful (blue, green, etc.) - they have high saturation
        # Text is nearly black, cream is desaturated
        r, g, b = tight_cropped[:, :, 0], tight_cropped[:, :, 1], tight_cropped[:, :, 2]

        # Calculate a simple "colorfulness" metric
        # Colored pixels have larger differences between RGB channels
        max_rgb = np.maximum(np.maximum(r, g), b)
        min_rgb = np.minimum(np.minimum(r, g), b)
        color_diff = max_rgb - min_rgb

        # Colored pixels: significant difference between channels AND not too dark AND not too bright
        is_colored = (color_diff > 30) & (max_rgb > 80) & (max_rgb < 240)

        # Count colored pixels per row - only exclude rows with SIGNIFICANT color (>5% of row)
        row_color_percentage = np.sum(is_colored, axis=1) / is_colored.shape[1]
        row_has_significant_color = row_color_percentage > 0.05

        # Create output - white background
        result = (
            np.ones((tight_cropped.shape[0], tight_cropped.shape[1]), dtype=np.uint8)
            * 255
        )

        # Only include dark text from rows WITHOUT significant colored pixels
        is_dark = np.all(tight_cropped < 100, axis=2)

        # Vectorized masking: rows without significant color AND dark pixels
        mask = (~row_has_significant_color[:, np.newaxis]) & is_dark
        result[mask] = 0

        # Convert to PIL
        result_image = Image.fromarray(result, mode="L")

        # Upscale for better OCR
        scale = 2
        new_size = (result_image.width * scale, result_image.height * scale)
        result_image = result_image.resize(new_size, Image.Resampling.BILINEAR)

        # Debug: save intermediate images
        if self.debug_mode:
            Image.fromarray(mask.astype(np.uint8) * 255).save(
                self.debug_dir / "tooltip_mask.png"
            )
            Image.fromarray(cropped).save(self.debug_dir / "tooltip_cropped.png")
            Image.fromarray(tight_cropped).save(
                self.debug_dir / "tooltip_tight_cropped.png"
            )
            Image.fromarray(is_colored.astype(np.uint8) * 255).save(
                self.debug_dir / "tooltip_colored.png"
            )
            Image.fromarray(
                row_has_significant_color[:, np.newaxis]
                .repeat(tight_cropped.shape[1], axis=1)
                .astype(np.uint8)
                * 255
            ).save(self.debug_dir / "tooltip_colored_rows.png")
            Image.fromarray(result).save(self.debug_dir / "tooltip_text_mask.png")

        return result_image

    def extract_text(
        self,
        image: Image.Image,
        *,
        single_line: bool = True,
        whitelist: str | None = None,
    ) -> OCRResult:
        """
        Extract text from an image.

        Args:
            image: Preprocessed image
            single_line: Whether to expect a single line of text
            whitelist: Character whitelist for OCR
        """
        # Build Tesseract config
        config_parts = []

        # Page segmentation mode
        if single_line:
            config_parts.append("--psm 7")  # Single line
        else:
            config_parts.append("--psm 6")  # Block of text

        # Character whitelist
        if whitelist:
            config_parts.append(f'-c tessedit_char_whitelist="{whitelist}"')

        config = " ".join(config_parts)

        try:
            # Get text with confidence data
            data = pytesseract.image_to_data(
                image, config=config, output_type=pytesseract.Output.DICT
            )

            # Extract text and calculate average confidence
            texts = []
            confidences = []

            for i, text in enumerate(data["text"]):
                conf = data["conf"][i]
                if text.strip() and conf > 0:
                    texts.append(text.strip())
                    confidences.append(conf)

            raw_text = " ".join(texts)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0

            # Clean up text
            cleaned = self._clean_text(raw_text)

            return OCRResult(
                text=cleaned or None,
                confidence=avg_confidence,
                raw_text=raw_text,
            )

        except pytesseract.TesseractError as e:
            if self.debug_mode:
                logger.error(f"OCR Error: {e}")
            return OCRResult(text=None, confidence=0, raw_text="")

    @staticmethod
    def _clean_text(text: str) -> str:
        """Clean OCR output text."""
        # Remove common OCR artifacts
        text = re.sub(r"[|\\/_]", "", text)
        # Normalize whitespace
        text = re.sub(r"\s+", " ", text)
        # Strip
        return text.strip()

    def check_trigger(self, region: RegionMixin) -> bool:
        """
        Check if the trigger word (INVENTORY) is visible in the region.

        This is optimized for speed - we just need to detect the word,
        not extract it perfectly.
        """
        # Capture
        image = self.capture_region(region)

        # Preprocess
        processed = self.preprocess_for_ocr(image, invert=True, scale=2)

        # Save debug image if enabled
        if self.debug_mode:
            image.save(self.debug_dir / "trigger_raw.png")
            processed.save(self.debug_dir / "trigger_processed.png")

        # Extract with limited whitelist for speed using image_to_string (faster than image_to_data)
        config = f'--psm 7 -c tessedit_char_whitelist="{string.ascii_uppercase}"'
        try:
            text = pytesseract.image_to_string(processed, config=config)
            if text:
                # Fuzzy match - allow for some OCR errors
                return self._fuzzy_match(text.strip().upper(), self.TRIGGER_WORD)
        except pytesseract.TesseractError:
            pass

        return False

    def extract_item_name_at_cursor(self) -> str | None:
        """
        Extract item name from tooltip near the cursor.

        Captures area around cursor, finds the tooltip, and extracts the item name.
        Returns the cleaned item name or None if extraction fails.
        """
        # Capture around cursor
        image, _ = self.capture_around_cursor()

        # Save debug image if enabled
        if self.debug_mode:
            image.save(self.debug_dir / "tooltip_capture_raw.png")

        # The tooltip has a cream/beige background with dark text
        # We need to find the item name which is the large bold text
        # It appears after "ACTIONS" header and category tags

        # Strategy: Look for the item name area
        # The item name is typically in the upper portion of the tooltip
        # in large dark text on the cream background

        # First, let's try to find and extract from the whole capture
        # using appropriate preprocessing for dark text on light background

        processed = self.preprocess_tooltip(image)

        if self.debug_mode:
            processed.save(self.debug_dir / "tooltip_capture_processed.png")

        # Extract all text from the tooltip area
        try:
            # Use PSM 6 for block of text, then parse out the item name
            text = pytesseract.image_to_string(processed, config="--psm 6")

            logger.debug(f"Raw tooltip OCR:\n{text}")

            # Parse the text to find the item name
            item_name = self.parse_item_name_from_tooltip(text)

            if item_name:
                logger.debug(f"Extracted item name: '{item_name}'")
                return item_name

        except pytesseract.TesseractError as e:
            logger.error(f"OCR Error: {e}")

        return None

    def parse_item_name_from_tooltip(self, text: str) -> str | None:
        """
        Parse item name from tooltip OCR text.

        Item names are in ALL CAPS (like "ADVANCED ELECTRICAL COMPONENTS")
        and may span multiple lines.
        """
        lines = [line.strip() for line in text.split("\n") if line.strip()]

        if not lines:
            return None

        # Find consecutive ALL CAPS lines (the item name)
        name_lines = []
        found_name = False

        for line in lines:
            # Clean the line
            cleaned = self._clean_text(line)
            if not cleaned or len(cleaned) < 2:
                continue

            # Check if line is ALL CAPS (item name style)
            # Allow spaces, numbers, and punctuation
            alpha_chars = [c for c in cleaned if c.isalpha()]
            if not alpha_chars:
                continue

            is_all_caps = all(c.isupper() for c in alpha_chars)

            if is_all_caps and len(cleaned) >= 3:
                # This looks like an item name line
                name_lines.append(cleaned)
                found_name = True
            elif found_name:
                # We were collecting name lines but hit a non-caps line
                # This means the name is complete
                break

        if name_lines:
            # Join multi-line names with a space
            return " ".join(name_lines)

        return None

    def extract_item_name(self, region: RegionMixin) -> str | None:
        """
        Legacy method - extract item name from a fixed region.
        Kept for calibration tool compatibility.
        """
        image = self.capture_region(region)
        processed = self.preprocess_tooltip(image)

        if self.debug_mode:
            image.save(self.debug_dir / "tooltip_raw.png")
            processed.save(self.debug_dir / "tooltip_processed.png")

        try:
            text = pytesseract.image_to_string(processed, config="--psm 6")
            return self.parse_item_name_from_tooltip(text)
        except pytesseract.TesseractError as e:
            logger.error(f"OCR Error: {e}")
            return None

    @staticmethod
    def _fuzzy_match(text: str, target: str, threshold: float = 0.7) -> bool:
        """
        Check if text approximately matches target.
        Uses simple character overlap ratio.
        """
        if not text:
            return False

        # Check if target appears as substring
        if target in text:
            return True

        # Check character overlap
        text_chars = set(text)
        target_chars = set(target)
        overlap = len(text_chars & target_chars) / len(target_chars)

        return overlap >= threshold


class OCREngineManager:
    """Manages the singleton OCREngine instance."""

    _instance: OCREngine | None = None

    @classmethod
    def get(cls) -> OCREngine:
        """Get the OCR engine instance, creating it if needed."""
        if cls._instance is None:
            cls._instance = OCREngine()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (mainly for testing or reloading settings)."""
        cls._instance = None


def get_ocr_engine() -> OCREngine:
    """Get the singleton OCR engine instance."""
    return OCREngineManager.get()
