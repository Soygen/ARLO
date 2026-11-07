"""Action colors and tooltip detail formatting (toolkit-agnostic)."""

from __future__ import annotations

import html
import re

ACTION_COLORS: dict[str, str] = {
    "SELL": "#FFD700",
    "RECYCLE": "#00CED1",
    "KEEP": "#32CD32",
    "CRAFTING": "#32CD32",
    "USE": "#FF69B4",
    "TRASH": "#FF4444",
    "UNKNOWN": "#888888",
}

BG_DARK = "#12121f"
BG_PANEL = "#1a1a2e"
BORDER_COLOR = "#3a3a5c"
TEXT_DIM = "#9999bb"
TEXT_INFO = "#c4a0ff"

RECYCLE_SYMBOL = "\u267b"


def normalize_keep_labels(keep_for: str) -> str:
    text = (keep_for or "").strip()
    if not text:
        return ""
    text = html.unescape(text)
    text = text.replace("[[", "").replace("]]", "")
    text = re.sub(r"\bQuests(?!:)\s+", "Quests: ", text)
    text = re.sub(r"\bProjects(?!:)\s+", "Projects: ", text)
    text = re.sub(r"\bWorkshop(?!:)\s+", "Workshop: ", text)
    text = re.sub(
        r"\bResearch(?!:)(?!\s+(?:Station|Points)\b)\s+",
        "Research: ",
        text,
    )
    text = re.sub(r"\bCrafting(?!:)\s+", "Crafting: ", text)
    return re.sub(r"\bAmplify(?!:)\s+", "Amplify: ", text)


def format_detail_parts(
    *,
    recycle_for: str | None,
    keep_for: str | None,
) -> tuple[str, str]:
    keep = normalize_keep_labels(keep_for or "")
    recycle_raw = (recycle_for or "").strip()
    recycle = f"{RECYCLE_SYMBOL} {recycle_raw}" if recycle_raw else ""
    return keep, recycle


def format_detail_text(
    *,
    recycle_for: str | None,
    keep_for: str | None,
) -> str:
    keep, recycle = format_detail_parts(
        recycle_for=recycle_for,
        keep_for=keep_for,
    )
    return "\n".join(part for part in (keep, recycle) if part)
