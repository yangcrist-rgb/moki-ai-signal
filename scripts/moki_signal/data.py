from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

from .contracts import Instrument


START_DATE = "2023-06-01"
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
SINA_DAILY_URL = "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var/US_MinKService.getDailyK"
VALIDATION_PROVIDER = "sina-finance-us-daily"


def canonical_ticker(ticker: str) -> str:
    value = ticker.strip().upper().replace(" ", "")
    if not TICKER_RE.fullmatch(value):
        raise ValueError("Ticker must contain only US ticker letters, digits, dots, or hyphens.")
    return value.replace(".", "-")


def default_as_of() -> pd.Timestamp:
    now = datetime.now(ZoneInfo("America/New_York"))
    value = now.date()
    if now.weekday() < 5 and now.time() < time(16, 15):
        value -= timedelta(days=1)
    return pd.Timestamp(value)


def _parse_chart(payload: dict, original_ticker: str) -> tuple[pd.DataFrame, Instrument]:
    chart = payload.get("chart", {})
    error = chart.get("error")
    result = chart.get("result")
    if error or not result:
        message = error.get("description", "Yahoo returned no chart data") if isinstance(error, dict) else "Yahoo returned no chart data"
        raise ValueError(message)
    root = result[0]
    quote_data = root["indicators"]["quote"][0]
    timestamps = root.get("timestamp", [])
    frame = pd.DataFrame(
        {
            "Date": [datetime.fromtimestamp(value, ZoneInfo("UTC")).date() for value in timestamps],
            "Open": quote_data.get("open", []),
            "High": quote_data.get("high", []),
            "Low": quote_data.get("low", []),
            "Close": quote_data.get("close", []),
            "Volume": quote_data.get("volume", []),
        }
    )
    frame["Date"] = pd.to_datetime(frame["Date"])
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"]).reset_index(drop=True)
    meta = root.get("meta", {})
    instrument = Instrument(
        input_ticker=original_ticker,
        ticker=str(meta.get("symbol") or original_ticker).upper(),
        company_name=str(meta.get("longName") or meta.get("shortName") or original_ticker),
        currency=str(meta.get("currency") or "USD"),
        exchange=str(meta.get("fullExchangeName") or meta.get("exchangeName") or "US"),
    )
    return frame, instrument


def _load_cache(cache_file: Path) -> tuple[pd.DataFrame, Instrument]:
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    frame = pd.DataFrame(payload["rows"])
    frame["Date"] = pd.to_datetime(frame["Date"])
    return frame, Instrument(**payload["instrument"])


def _store_cache(cache_file: Path, frame: pd.DataFrame, instrument: Instrument) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "instrument": instrument.__dict__,
        "rows": [{**row, "Date": pd.Timestamp(row["Date"]).strftime("%Y-%m-%d")} for row in frame.to_dict("records")],
    }
    cache_file.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _parse_sina_daily(payload: str) -> pd.DataFrame:
    """Parse Sina's US daily JSONP response into the MOKI OHLCV schema.

    Adapted and modified from global-stock-data's ``us_stock_kline_sina``
    implementation. The upstream code is Apache-2.0 licensed; see
    ``licenses/global-stock-data-APACHE-2.0.txt``.
    """
    match = re.search(r"\((\s*\[.*\]\s*)\)", payload, flags=re.DOTALL)
    if not match:
        raise ValueError("Sina returned an unrecognized daily K-line response")
    items = json.loads(match.group(1))
    if not isinstance(items, list) or not items:
        raise ValueError("Sina returned no daily K-line rows")
    frame = pd.DataFrame(
        {
            "Date": [item.get("d") for item in items],
            "Open": [item.get("o") for item in items],
            "High": [item.get("h") for item in items],
            "Low": [item.get("l") for item in items],
            "Close": [item.get("c") for item in items],
            "Volume": [item.get("v") for item in items],
        }
    )
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    for column in ("Open", "High", "Low", "Close", "Volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["Date", "Open", "High", "Low", "Close"]).sort_values("Date").reset_index(drop=True)


def load_sina_daily(ticker: str, *, num: int = 120, timeout: int = 15) -> pd.DataFrame:
    """Fetch recent US daily bars from Sina for independent validation only."""
    normalized = canonical_ticker(ticker)
    query = urlencode({"symbol": normalized, "num": max(20, int(num))})
    request = Request(
        f"{SINA_DAILY_URL}?{query}",
        headers={
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "MOKI-AI-Signal/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8", errors="replace")
    return _parse_sina_daily(payload)


def compare_daily_frames(
    primary: pd.DataFrame,
    reference: pd.DataFrame,
    *,
    lookback: int = 10,
    relative_tolerance: float = 0.001,
    absolute_tolerance: float = 0.02,
) -> dict[str, object]:
    """Compare recent OHLC cells without changing the primary strategy input."""
    columns = ["Date", "Open", "High", "Low", "Close"]
    if len(primary):
        cutoff = pd.Timestamp(primary["Date"].max())
        reference = reference[reference["Date"] <= cutoff]
    merged = primary[columns].merge(reference[columns], on="Date", suffixes=("_primary", "_reference"))
    merged = merged.sort_values("Date").tail(max(1, int(lookback))).reset_index(drop=True)
    primary_last = pd.Timestamp(primary["Date"].iloc[-1]).strftime("%Y-%m-%d") if len(primary) else None
    reference_last = pd.Timestamp(reference["Date"].iloc[-1]).strftime("%Y-%m-%d") if len(reference) else None
    if merged.empty:
        return {
            "status": "warning",
            "reason": "no-overlapping-trading-dates",
            "checked_bars": 0,
            "primary_last_date": primary_last,
            "reference_last_date": reference_last,
        }

    mismatches: list[dict[str, object]] = []
    total_cells = len(merged) * 4
    matched_cells = 0
    for _, row in merged.iterrows():
        for field in ("Open", "High", "Low", "Close"):
            primary_value = float(row[f"{field}_primary"])
            reference_value = float(row[f"{field}_reference"])
            tolerance = max(absolute_tolerance, max(abs(primary_value), abs(reference_value)) * relative_tolerance)
            difference = abs(primary_value - reference_value)
            if difference <= tolerance:
                matched_cells += 1
            else:
                mismatches.append(
                    {
                        "date": pd.Timestamp(row["Date"]).strftime("%Y-%m-%d"),
                        "field": field.lower(),
                        "primary": round(primary_value, 6),
                        "reference": round(reference_value, 6),
                        "difference": round(difference, 6),
                    }
                )

    enough_overlap = len(merged) >= min(5, max(1, int(lookback)))
    dates_aligned = primary_last == reference_last
    passed = enough_overlap and dates_aligned and not mismatches
    return {
        "status": "passed" if passed else "warning",
        "reason": None if passed else "date-or-price-mismatch",
        "checked_bars": int(len(merged)),
        "matched_price_cells": int(matched_cells),
        "total_price_cells": int(total_cells),
        "primary_last_date": primary_last,
        "reference_last_date": reference_last,
        "latest_common_date": pd.Timestamp(merged["Date"].iloc[-1]).strftime("%Y-%m-%d"),
        "tolerance": {"relative": relative_tolerance, "absolute": absolute_tolerance},
        "mismatches": mismatches[:20],
    }


def validate_yahoo_daily(frame: pd.DataFrame, ticker: str, *, lookback: int = 10) -> dict[str, object]:
    """Best-effort Sina cross-check; failures never replace or mutate Yahoo data."""
    audit: dict[str, object] = {
        "provider": VALIDATION_PROVIDER,
        "role": "validation-only",
        "affects_strategy_input": False,
    }
    try:
        reference = load_sina_daily(ticker, num=max(120, min(2000, len(frame) + 20)))
        audit.update(compare_daily_frames(frame, reference, lookback=lookback))
    except Exception as exc:
        audit.update(
            {
                "status": "unavailable",
                "reason": "validation-provider-error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    return audit


def load_yahoo_daily(ticker: str, *, as_of: pd.Timestamp, cache_dir: Path, refresh: bool = False) -> tuple[pd.DataFrame, Instrument, bool]:
    normalized = canonical_ticker(ticker)
    cache_file = cache_dir / f"{normalized}.json"
    end = (as_of + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    start_epoch = int(pd.Timestamp(START_DATE, tz="UTC").timestamp())
    end_epoch = int(pd.Timestamp(end, tz="UTC").timestamp())
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{quote(normalized)}?period1={start_epoch}&period2={end_epoch}&interval=1d&events=history"
    try:
        request = Request(url, headers={"User-Agent": "MOKI-AI-Signal/1.0"})
        with urlopen(request, timeout=30) as response:
            frame, instrument = _parse_chart(json.load(response), normalized)
        _store_cache(cache_file, frame, instrument)
        stale = False
    except Exception:
        if refresh or not cache_file.exists():
            raise
        frame, instrument = _load_cache(cache_file)
        stale = True
    frame = frame[(frame["Date"] >= pd.Timestamp(START_DATE)) & (frame["Date"] <= as_of)].reset_index(drop=True)
    if len(frame) < 20:
        raise ValueError(f"Not enough daily OHLC data for {normalized}: {len(frame)} bars")
    return frame, instrument, stale
