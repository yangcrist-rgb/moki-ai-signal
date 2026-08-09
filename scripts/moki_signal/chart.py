from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from .contracts import Instrument, StrategyResult
from .render import FONT_BOLD, FONT_REGULAR, TEMPLATE_ID, _brand_lockup, _fit_title, _font


def render_chart(frame: pd.DataFrame, instrument: Instrument, result: StrategyResult, *, output: Path, days: int, stale_cache: bool) -> dict[str, object]:
    last_date = pd.Timestamp(frame["Date"].iloc[-1])
    cutoff = last_date - pd.Timedelta(days=days - 1)
    visible = frame.loc[frame["Date"] >= cutoff].copy().reset_index().rename(columns={"index": "source_index"})
    if visible.empty:
        raise ValueError("No rows in the requested display window")
    up_color, down_color = "#10B878", "#FF3B43"
    buy_color, sell_color = "#17933A", "#EC28D9"
    wick_color, grid_color, text_color = "#73777D", "#D6D8DC", "#0B0F0E"
    width, height = 1920, 1128
    left, top, right, bottom = 85, 160, 1825, 1010
    chart_width, chart_height = right - left, bottom - top
    image = Image.new("RGB", (width, height), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    draw.rectangle((left, top, right, bottom), fill="#FBFBFC")
    price_min, price_max = float(visible["Low"].min()), float(visible["High"].max())
    price_range = max(price_max - price_min, max(price_max, 1) * 0.08)
    annotation_pad = price_range * 0.065
    y_min, y_max = price_min - annotation_pad * 1.9, price_max + annotation_pad * 1.9

    def x_for(index: int) -> float:
        return left + (index + 0.5) * chart_width / len(visible)

    def y_for(price: float) -> float:
        return bottom - (price - y_min) / (y_max - y_min) * chart_height

    tick_positions = np.unique(np.linspace(0, len(visible) - 1, min(16, len(visible)), dtype=int))
    for position in tick_positions:
        x = round(x_for(int(position)))
        draw.line((x, top, x, bottom), fill=grid_color, width=1)
    for value in np.linspace(y_min, y_max, 6):
        y = round(y_for(float(value)))
        draw.line((left, y, right, y), fill=grid_color, width=1)
        draw.text((right + 10, y - 10), f"{value:,.0f}", font=_font(FONT_REGULAR, 17), fill="#373A3E")

    candle_width = max(8, min(20, int(chart_width / len(visible) * 0.56)))
    for index, row in visible.iterrows():
        open_price, high_price = float(row["Open"]), float(row["High"])
        low_price, close_price = float(row["Low"]), float(row["Close"])
        color = up_color if close_price >= open_price else down_color
        x = round(x_for(index))
        high_y, low_y = round(y_for(high_price)), round(y_for(low_price))
        open_y, close_y = round(y_for(open_price)), round(y_for(close_price))
        draw.line((x, high_y, x, low_y), fill=wick_color, width=2)
        body_top, body_bottom = min(open_y, close_y), max(open_y, close_y)
        draw.rectangle((x - candle_width // 2, body_top, x + candle_width // 2, max(body_top + 3, body_bottom)), fill=color, outline=color)

    source_to_visible = {int(source): index for index, source in enumerate(visible["source_index"])}
    signal_count = 0
    for event in result.confirmed:
        if event.pivot_index not in source_to_visible:
            continue
        signal_count += 1
        row = frame.iloc[event.pivot_index]
        x = round(x_for(source_to_visible[event.pivot_index]))
        if event.side == "B":
            target_price, label_price, color = float(row["Low"]), float(row["Low"]) - annotation_pad, buy_color
        else:
            target_price, label_price, color = float(row["High"]), float(row["High"]) + annotation_pad, sell_color
        target_y, label_y = round(y_for(target_price)), round(y_for(label_price))
        label_font = _font(FONT_REGULAR, 23)
        label_width = draw.textbbox((0, 0), event.side, font=label_font)[2]
        if event.side == "B":
            draw.text((x - label_width / 2, label_y), event.side, font=label_font, fill=color)
            line_start, arrow_points = label_y - 4, [(x, target_y), (x - 6, target_y + 6), (x + 6, target_y + 6)]
        else:
            draw.text((x - label_width / 2, label_y - 24), event.side, font=label_font, fill=color)
            line_start, arrow_points = label_y + 3, [(x, target_y), (x - 6, target_y - 6), (x + 6, target_y - 6)]
        draw.line((x, line_start, x, target_y), fill=color, width=2)
        draw.polygon(arrow_points, fill=color)

    for position in tick_positions:
        label = visible["Date"].iloc[int(position)].strftime("%m/%d")
        x = round(x_for(int(position)))
        draw.text((x - 22, bottom + 18), label, font=_font(FONT_REGULAR, 17), fill="#373A3E")

    draw.text((85, 42), "MOKI AI Signal", font=_font(FONT_BOLD, 38), fill=text_color)
    title = f"{instrument.company_name} [{instrument.ticker}.US]"
    title_font = _fit_title(draw, title, 1050)
    title_width = draw.textbbox((0, 0), title, font=title_font)[2]
    draw.text(((width - title_width) / 2, 18), title, font=title_font, fill=text_color)
    subtitle = "Authorized open-source reconstruction | 8% close-price ZigZag | RSI(14) pivot snap"
    subtitle_font = _font(FONT_REGULAR, 18)
    subtitle_width = draw.textbbox((0, 0), subtitle, font=subtitle_font)[2]
    draw.text(((width - subtitle_width) / 2, 72), subtitle, font=subtitle_font, fill="#62666B")

    lockup = _brand_lockup()
    image.paste(lockup, (right - lockup.width, 20), lockup)
    if result.pending:
        operator = ">=" if result.pending.side == "B" else "<="
        pending = f"Pending {result.pending.side}: close {operator} ${result.pending.trigger_close:,.2f}"
        pending_font = _font(FONT_REGULAR, 18)
        pending_width = draw.textbbox((0, 0), pending, font=pending_font)[2]
        draw.text((right - pending_width, 96), pending, font=pending_font, fill="#62666B")

    footer_prefix = "Yahoo Finance daily OHLC"
    if stale_cache:
        footer_prefix += " (cached after download failure)"
    footer = f"{footer_prefix} | {visible['Date'].iloc[0]:%Y-%m-%d} to {last_date:%Y-%m-%d} | B/S labels are plotted on pivot bars and become known only on later confirmation bars."
    draw.text((85, 1085), footer, font=_font(FONT_REGULAR, 16), fill="#6B6F75")
    price_label = "Price (USD)"
    label_layer = Image.new("RGBA", (180, 34), (255, 255, 255, 0))
    ImageDraw.Draw(label_layer).text((0, 3), price_label, font=_font(FONT_REGULAR, 18), fill="#333333")
    label_layer = label_layer.rotate(90, expand=True)
    image.paste(label_layer, (1880, 510), label_layer)
    image.save(output, format="PNG", optimize=True)
    return {"display_start": cutoff.strftime("%Y-%m-%d"), "visible_signals": signal_count, "last_date": last_date.strftime("%Y-%m-%d"), "template_id": TEMPLATE_ID}
