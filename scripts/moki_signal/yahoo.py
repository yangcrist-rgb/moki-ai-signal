from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener
from zoneinfo import ZoneInfo

import pandas as pd


BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
QUOTE_URL = "https://query2.finance.yahoo.com/v7/finance/quote"
OPTIONS_URL = "https://query2.finance.yahoo.com/v7/finance/options/{symbol}"
CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
QUOTE_FIELDS = (
    "shortName,longName,marketCap,regularMarketPrice,regularMarketChangePercent,regularMarketPreviousClose,earningsTimestamp,earningsTimestampStart,"
    "earningsTimestampEnd,isEarningsDateEstimate,epsForward,epsCurrentYear,epsTrailingTwelveMonths,quoteType,"
    "marketState,postMarketPrice,postMarketChangePercent,preMarketPrice,preMarketChangePercent"
)


class YahooClient:
    """Small Yahoo Finance client; option chains and batch quotes require a session crumb."""

    def __init__(self, *, timeout: int = 20, retries: int = 3) -> None:
        self.timeout = timeout
        self.retries = retries
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self._crumb: str | None = None
        self._crumb_lock = threading.Lock()

    def _get(self, url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                request = Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "application/json,text/plain,*/*"})
                with self._opener.open(request, timeout=self.timeout) as response:
                    return response.read()
            except HTTPError as exc:
                last_error = exc
                if exc.code not in (429, 500, 502, 503, 504):
                    raise
            except (URLError, TimeoutError) as exc:
                last_error = exc
            time.sleep(1.5 * (2**attempt))
        assert last_error is not None
        raise last_error

    def _get_json(self, url: str) -> dict:
        return json.loads(self._get(url).decode("utf-8"))

    def crumb(self) -> str:
        with self._crumb_lock:
            if self._crumb is None:
                try:
                    self._get("https://fc.yahoo.com")
                except HTTPError:
                    pass
                self._crumb = self._get(CRUMB_URL).decode("utf-8").strip()
            return self._crumb

    def history(self, symbol: str, *, range_: str = "1y") -> pd.DataFrame:
        url = CHART_URL.format(symbol=quote(symbol)) + "?" + urlencode({"range": range_, "interval": "1d", "events": "history"})
        chart = self._get_json(url).get("chart", {})
        if chart.get("error") or not chart.get("result"):
            raise ValueError(f"Yahoo returned no chart data for {symbol}")
        root = chart["result"][0]
        quote_data = root["indicators"]["quote"][0]
        timezone = ZoneInfo(root.get("meta", {}).get("exchangeTimezoneName") or "America/New_York")
        frame = pd.DataFrame(
            {
                "Date": [datetime.fromtimestamp(value, timezone).date() for value in root.get("timestamp", [])],
                "Open": quote_data.get("open", []),
                "High": quote_data.get("high", []),
                "Low": quote_data.get("low", []),
                "Close": quote_data.get("close", []),
                "Volume": quote_data.get("volume", []),
            }
        )
        frame["Date"] = pd.to_datetime(frame["Date"])
        return frame.dropna(subset=["Close"]).drop_duplicates("Date", keep="last").reset_index(drop=True)

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for start in range(0, len(symbols), 50):
            batch = symbols[start : start + 50]
            query = urlencode({"symbols": ",".join(batch), "fields": QUOTE_FIELDS, "crumb": self.crumb()})
            payload = self._get_json(f"{QUOTE_URL}?{query}")
            for item in payload.get("quoteResponse", {}).get("result", []) or []:
                result[item["symbol"]] = item
        return result

    def option_chain(self, symbol: str, expiration: int | None = None) -> dict:
        params = {"crumb": self.crumb()}
        if expiration is not None:
            params["date"] = str(int(expiration))
        payload = self._get_json(OPTIONS_URL.format(symbol=quote(symbol)) + "?" + urlencode(params))
        results = payload.get("optionChain", {}).get("result") or []
        if not results:
            raise ValueError(f"Yahoo returned no option chain for {symbol}")
        return results[0]
