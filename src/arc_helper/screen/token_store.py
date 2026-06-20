"""Persistence for the xdg-desktop-portal ScreenCast restore token.

The token lets later runs reuse the user's one-time screen-share approval.
Tokens are single-use: every portal Start() returns a fresh one to save.
"""

from pathlib import Path


def _default_path() -> Path:
    from arc_helper.config import APP_DIR

    return APP_DIR / ".screencast_restore_token"


def load_token(path: Path | None = None) -> str | None:
    target = path if path is not None else _default_path()
    try:
        text = target.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def save_token(token: str, path: Path | None = None) -> None:
    """Persist the token; raises OSError on failure (caller logs and continues)."""
    target = path if path is not None else _default_path()
    target.write_text(token, encoding="utf-8")
    target.chmod(0o600)


def clear_token(path: Path | None = None) -> None:
    target = path if path is not None else _default_path()
    target.unlink(missing_ok=True)
