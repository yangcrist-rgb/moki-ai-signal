from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from .contracts import Instrument, StrategyResult


ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "assets"
FONT_REGULAR = ASSETS / "fonts" / "LiberationSans-Regular.ttf"
FONT_BOLD = ASSETS / "fonts" / "LiberationSans-Bold.ttf"
REFERENCE_LOGO = ASSETS / "brand" / "moki-brand-reference.png"
TEMPLATE_ID = "moki-ai-signal-v1"


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


def _remove_connected_background(image: Image.Image, threshold: int = 32) -> Image.Image:
    rgba = image.convert("RGBA")
    source = rgba.load()
    width, height = rgba.size
    base = source[0, 0][:3]
    alpha = Image.new("L", rgba.size, 255)
    alpha_pixels = alpha.load()
    queue: deque[tuple[int, int]] = deque()
    visited = set()
    for x in range(width):
        queue.extend(((x, 0), (x, height - 1)))
    for y in range(height):
        queue.extend(((0, y), (width - 1, y)))
    while queue:
        x, y = queue.popleft()
        if (x, y) in visited or not (0 <= x < width and 0 <= y < height):
            continue
        visited.add((x, y))
        pixel = source[x, y][:3]
        if max(abs(pixel[channel] - base[channel]) for channel in range(3)) > threshold:
            continue
        alpha_pixels[x, y] = 0
        queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    rgba.putalpha(alpha)
    return rgba


def _brand_lockup() -> Image.Image:
    direct = ASSETS / "brand" / "moki-logo-horizontal.png"
    if direct.exists():
        image = Image.open(direct).convert("RGBA")
        image.thumbnail((255, 64), Image.Resampling.LANCZOS)
        return image
    reference = Image.open(REFERENCE_LOGO).convert("RGBA")
    word = _remove_connected_background(reference.crop((70, 145, 585, 290)))
    icon = _remove_connected_background(reference.crop((1100, 148, 1460, 508)))
    icon.thumbnail((56, 56), Image.Resampling.LANCZOS)
    word.thumbnail((176, 54), Image.Resampling.LANCZOS)
    lockup = Image.new("RGBA", (icon.width + 12 + word.width, max(icon.height, word.height)), (0, 0, 0, 0))
    lockup.alpha_composite(icon, (0, (lockup.height - icon.height) // 2))
    lockup.alpha_composite(word, (icon.width + 12, (lockup.height - word.height) // 2))
    return lockup


def _fit_title(draw: ImageDraw.ImageDraw, text: str, maximum_width: int) -> ImageFont.FreeTypeFont:
    for size in range(36, 23, -1):
        font = _font(FONT_BOLD, size)
        if draw.textbbox((0, 0), text, font=font)[2] <= maximum_width:
            return font
    return _font(FONT_BOLD, 23)


def write_signals(frame: pd.DataFrame, result: StrategyResult, target: Path) -> None:
    rows = []
    for event in result.confirmed:
        pivot = frame.iloc[event.pivot_index]
        confirmation = frame.iloc[event.confirmation_index]
        rows.append({"side": event.side, "pivot_date": pivot["Date"].strftime("%Y-%m-%d"), "pivot_close": round(float(pivot["Close"]), 4), "confirmation_date": confirmation["Date"].strftime("%Y-%m-%d"), "confirmation_close": round(float(confirmation["Close"]), 4), "confirmation_delay_bars": event.confirmation_index - event.pivot_index})
    columns = ["side", "pivot_date", "pivot_close", "confirmation_date", "confirmation_close", "confirmation_delay_bars"]
    pd.DataFrame(rows, columns=columns).to_csv(target, index=False, encoding="utf-8-sig")
