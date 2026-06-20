"""Live diagnostic for the screen backend.

Run:  uv run python -m arc_helper.screen.diag
Prints backend, resolution, scale and cursor samples; saves a center crop
to diag_frame.png in the app directory.
"""

import sys
import time

from arc_helper.config import APP_DIR
from arc_helper.screen import get_backend


def main() -> int:
    backend = get_backend()
    print(f"backend    : {backend.name}")
    width, height = backend.resolution()
    print(f"resolution : {width}x{height}")
    print(f"tk scale   : {backend.tk_scale:.4f}")

    for i in range(3):
        cursor = backend.cursor_position()
        print(f"cursor     : ({cursor.x}, {cursor.y})")
        if i < 2:
            time.sleep(1)

    bbox = (width // 2 - 200, height // 2 - 100, width // 2 + 200, height // 2 + 100)
    image = backend.grab(bbox)
    out_path = APP_DIR / "diag_frame.png"
    image.save(out_path)
    print(f"frame crop : {image.size[0]}x{image.size[1]} -> {out_path}")
    backend.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
