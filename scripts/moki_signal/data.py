from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

from .contracts import Instrument


START_DATE = "2023-06-01"
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


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
