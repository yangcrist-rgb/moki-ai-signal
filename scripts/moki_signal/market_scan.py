from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from .options_math import (
    Contract,
    expirations_in_window,
    gex_summary,
    parse_contracts,
    pick_by_delta,
    pick_expiration,
    spot_for_put_delta,
    volume_anomaly,
)
from .yahoo import YahooClient


SCAN_ID = "us-market-indicator"
SCAN_VERSION = "0.1.0"
EASTERN = ZoneInfo("America/New_York")
DEFAULT_RATE = 0.04


def _pct(new: float, old: float) -> float | None:
    if old is None or new is None or old == 0 or math.isnan(old) or math.isnan(new):
        return None
    return round((new / old - 1) * 100, 2)


def classify_trend(close: float, sma: dict[int, float | None], sma20_slope: float | None) -> str:
    sma20, sma50 = sma.get(20), sma.get(50)
    if sma20 is None or sma50 is None:
        return "insufficient-data"
    if close > sma20 > sma50 and (sma20_slope or 0) > 0:
        return "bullish"
    if close < sma20 < sma50 and (sma20_slope or 0) < 0:
        return "bearish"
    return "range"


def trend_snapshot(frame: pd.DataFrame, windows: list[int], return_days: tuple[int, ...] = ()) -> dict[str, Any]:
    close = frame["Close"].astype(float).reset_index(drop=True)
    last = float(close.iloc[-1])

    def back(bars: int) -> float | None:
        return float(close.iloc[-1 - bars]) if len(close) > bars else None

    sma = {window: (round(float(close.tail(window).mean()), 4) if len(close) >= window else None) for window in windows}
    sma20_series = close.rolling(20).mean()
    slope = None
    if len(close) >= 25 and not math.isnan(sma20_series.iloc[-6]):
        slope = float(sma20_series.iloc[-1] - sma20_series.iloc[-6])
    extra = {f"ret_{days}d": _pct(last, back(days)) for days in return_days if days not in (1, 5, 20)}
    high_20 = float(frame["High"].tail(20).max()) if "High" in frame else None
    low_20 = float(frame["Low"].tail(20).min()) if "Low" in frame else None
    return {
        "date": pd.Timestamp(frame["Date"].iloc[-1]).strftime("%Y-%m-%d"),
        "close": round(last, 4),
        "ret_1d": _pct(last, back(1)),
        "ret_5d": _pct(last, back(5)),
        "ret_20d": _pct(last, back(20)),
        **extra,
        "sma": {str(window): value for window, value in sma.items()},
        "above_sma": {str(window): (value is not None and last > value) for window, value in sma.items()},
        "sma20_slope_5d": round(slope, 4) if slope is not None else None,
        "high_20d": round(high_20, 4) if high_20 is not None else None,
        "low_20d": round(low_20, 4) if low_20 is not None else None,
        "trend": classify_trend(last, sma, slope),
    }


def macro_snapshot(frame: pd.DataFrame, unit: str) -> dict[str, Any]:
    close = frame["Close"].astype(float).reset_index(drop=True)
    last = float(close.iloc[-1])
    out: dict[str, Any] = {"date": pd.Timestamp(frame["Date"].iloc[-1]).strftime("%Y-%m-%d"), "last": round(last, 3)}
    for label, bars in (("1d", 1), ("5d", 5), ("20d", 20)):
        if len(close) <= bars:
            out[f"chg_{label}"] = None
            continue
        old = float(close.iloc[-1 - bars])
        out[f"chg_{label}"] = round((last - old) * 100, 1) if unit == "yield" else _pct(last, old)
    out["change_unit"] = "bp" if unit == "yield" else "pct"
    return out


def rank_sectors(
    sectors: dict[str, dict], snapshots: dict[str, dict], leaders_per_sector: int, *, sector_days: int = 20, leader_days: int = 5
) -> list[dict[str, Any]]:
    sector_key, leader_key = f"ret_{sector_days}d", f"ret_{leader_days}d"
    ranked = []
    for key, sector in sectors.items():
        members = [ticker for ticker in sector["members"] if ticker in snapshots]
        if not members:
            continue
        etf = sector.get("etf")
        if etf and etf in snapshots and snapshots[etf].get(sector_key) is not None:
            score, basis = snapshots[etf][sector_key], f"{etf} {sector_days}d"
        else:
            values = [snapshots[ticker][sector_key] for ticker in members if snapshots[ticker].get(sector_key) is not None]
            score, basis = (round(sum(values) / len(values), 2) if values else None), f"equal-weight {sector_days}d"
        above20 = sum(1 for ticker in members if snapshots[ticker]["above_sma"].get("20"))
        above50 = sum(1 for ticker in members if snapshots[ticker]["above_sma"].get("50"))
        by_5d = sorted(members, key=lambda ticker: snapshots[ticker][leader_key] if snapshots[ticker].get(leader_key) is not None else -1e9, reverse=True)
        ranked.append(
            {
                "key": key,
                "name": sector.get("name", key),
                "score_20d": score,
                "score_basis": basis,
                "breadth_above_sma20": round(above20 / len(members) * 100),
                "breadth_above_sma50": round(above50 / len(members) * 100),
                "members": [
                    {"ticker": ticker, "ret_leader": snapshots[ticker].get(leader_key), "ret_sector": snapshots[ticker].get(sector_key), "trend": snapshots[ticker]["trend"]}
                    for ticker in by_5d
                ],
                "leaders": by_5d[: max(1, leaders_per_sector)],
            }
        )
    ranked.sort(key=lambda item: item["score_20d"] if item["score_20d"] is not None else -1e9, reverse=True)
    for rank, item in enumerate(ranked, start=1):
        item["rank"] = rank
    return ranked


def market_regime(index_snapshots: dict[str, dict], macro: dict[str, dict]) -> dict[str, Any]:
    core = [index_snapshots[ticker]["trend"] for ticker in ("SPY", "QQQ", "DIA", "IWM") if ticker in index_snapshots]
    bullish, bearish = core.count("bullish"), core.count("bearish")
    ten_year = macro.get("^TNX", {}).get("chg_20d")
    oil = macro.get("CL=F", {}).get("chg_20d")
    headwinds = []
    if ten_year is not None and ten_year >= 25:
        headwinds.append(f"10Y 20日上行 {ten_year}bp")
    if oil is not None and oil >= 10:
        headwinds.append(f"原油 20日上涨 {oil}%")
    if bullish >= 3 and not headwinds:
        label = "risk-on"
    elif bearish >= 3:
        label = "risk-off"
    else:
        label = "mixed"
    return {"label": label, "bullish_indices": bullish, "bearish_indices": bearish, "headwinds": headwinds}


def next_session(day: pd.Timestamp) -> pd.Timestamp:
    value = day + pd.Timedelta(days=1)
    while value.weekday() >= 5:
        value += pd.Timedelta(days=1)
    return value


def earnings_session(timestamp: int) -> str:
    local = datetime.fromtimestamp(timestamp, EASTERN)
    if local.hour < 9 or (local.hour == 9 and local.minute < 30):
        return "BMO"
    if local.hour >= 16:
        return "AMC"
    return "intraday/TBD"


def earnings_window(as_of: pd.Timestamp) -> tuple[int, int, str]:
    start = datetime.combine(as_of.date(), datetime.min.time(), EASTERN).replace(hour=16)
    today = next_session(as_of)
    end = datetime.combine(today.date(), datetime.min.time(), EASTERN) + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp()), today.strftime("%Y-%m-%d")


def build_earnings(
    quotes: dict[str, dict], as_of: pd.Timestamp, config: dict, now_epoch: int, implied_move: Callable[[str, int], float | None]
) -> dict[str, Any]:
    cfg = config["large_cap_earnings"]
    impact_map = cfg.get("impact_map", {})
    start, end, today_label = earnings_window(as_of)
    today_rows, upcoming = [], []
    lookahead = int(config.get("events", {}).get("lookahead_days", 30))
    horizon = now_epoch + lookahead * 86400
    for ticker, quote in sorted(quotes.items()):
        timestamp = quote.get("earningsTimestamp") or quote.get("earningsTimestampStart")
        if not timestamp:
            continue
        timestamp = int(timestamp)
        cap = quote.get("marketCap") or 0
        session = earnings_session(timestamp)
        row = {
            "ticker": ticker,
            "name": quote.get("shortName") or ticker,
            "market_cap_b": round(cap / 1e9, 1) if cap else None,
            "date": datetime.fromtimestamp(timestamp, EASTERN).strftime("%Y-%m-%d"),
            "session": session,
            "date_is_estimate": bool(quote.get("isEarningsDateEstimate")),
        }
        if start <= timestamp < end and cap >= cfg.get("min_market_cap", 3e11):
            impact = impact_map.get(ticker, {})
            row.update(
                {
                    "status": "reported-verify-actuals" if timestamp <= now_epoch else "scheduled",
                    "eps_forward": quote.get("epsForward"),
                    "eps_current_year": quote.get("epsCurrentYear"),
                    "eps_ttm": quote.get("epsTrailingTwelveMonths"),
                    "implied_move_pct": implied_move(ticker, timestamp),
                    "theme": impact.get("theme"),
                    "linked": impact.get("linked", []),
                }
            )
            today_rows.append(row)
        if now_epoch <= timestamp <= horizon:
            upcoming.append({**row, "type": "earnings"})
    for event in config.get("events", {}).get("manual", []):
        try:
            event_day = pd.Timestamp(event["date"])
        except (KeyError, ValueError):
            continue
        if as_of <= event_day <= as_of + pd.Timedelta(days=lookahead):
            upcoming.append({"ticker": event.get("ticker"), "name": event.get("name", ""), "date": event_day.strftime("%Y-%m-%d"), "type": event.get("type", "investor-day"), "session": event.get("session", "")})
    upcoming.sort(key=lambda row: (row["date"], row.get("ticker") or ""))
    today_rows.sort(key=lambda row: -(row.get("market_cap_b") or 0))
    return {"session_date": today_label, "lookahead_days": lookahead, "today": today_rows, "next_30d": upcoming}


class ChainCache:
    def __init__(self, client: YahooClient, now_epoch: int) -> None:
        self.client = client
        self.now_epoch = now_epoch
        self._chains: dict[tuple[str, int | None], dict] = {}

    def get(self, symbol: str, expiration: int | None = None) -> dict:
        key = (symbol, expiration)
        if key not in self._chains:
            chain = self.client.option_chain(symbol, expiration)
            self._chains[key] = chain
            if expiration is None and chain.get("options"):
                first = chain["options"][0].get("expirationDate")
                if first:
                    self._chains[(symbol, int(first))] = chain
        return self._chains[key]

    def expirations(self, symbol: str) -> list[int]:
        return [int(value) for value in self.get(symbol).get("expirationDates", [])]

    def spot(self, symbol: str) -> float | None:
        price = self.get(symbol).get("quote", {}).get("regularMarketPrice")
        return float(price) if price else None

    def contracts(self, symbol: str, expiration: int) -> list[Contract]:
        options = self.get(symbol, expiration).get("options") or [{}]
        block = options[0]
        return parse_contracts(block.get("calls", []), "call", self.now_epoch) + parse_contracts(block.get("puts", []), "put", self.now_epoch)


def straddle_move(cache: ChainCache, symbol: str, event_epoch: int) -> float | None:
    try:
        spot = cache.spot(symbol)
        expiration = next((value for value in cache.expirations(symbol) if value + 16 * 3600 >= event_epoch), None)
        if not spot or expiration is None:
            return None
        contracts = cache.contracts(symbol, expiration)
    except Exception:
        return None
    strikes = sorted({contract.strike for contract in contracts}, key=lambda strike: abs(strike - spot))
    for strike in strikes[:3]:
        call = next((c for c in contracts if c.kind == "call" and c.strike == strike and c.mid > 0), None)
        put = next((c for c in contracts if c.kind == "put" and c.strike == strike and c.mid > 0), None)
        if call and put:
            return round((call.mid + put.mid) / spot * 100, 2)
    return None


def _load_volume_history(path: Path) -> dict[str, dict[str, dict]]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def near_term_options(cache: ChainCache, symbol: str, rate: float, settings: dict, history: dict, as_of_label: str) -> dict[str, Any]:
    history_days = int(settings.get("history_days", 20))
    spot = cache.spot(symbol)
    if not spot:
        raise ValueError("no underlying price in option chain")
    near = [
        expiration
        for expiration in cache.expirations(symbol)
        if math.ceil((expiration + 16 * 3600 - cache.now_epoch) / 86400) <= settings["near_term_max_dte"]
    ][: settings["near_term_max_expirations"]]
    contracts: list[Contract] = []
    for expiration in near:
        contracts.extend(cache.contracts(symbol, expiration))
    call_volume = sum(contract.volume for contract in contracts if contract.kind == "call")
    put_volume = sum(contract.volume for contract in contracts if contract.kind == "put")
    total = call_volume + put_volume
    ticker_history = history.setdefault(symbol, {})
    prior = [entry["total"] for day, entry in sorted(ticker_history.items()) if day < as_of_label][-history_days:]
    ticker_history[as_of_label] = {"total": total, "calls": call_volume, "puts": put_volume}
    anomaly = volume_anomaly(total, prior, minimum_days=settings["anomaly_min_baseline_days"], multiple=settings["anomaly_multiple"])
    return {
        "spot": round(spot, 4),
        "expirations": [datetime.fromtimestamp(value, ZoneInfo("UTC")).strftime("%Y-%m-%d") for value in near],
        "volume": {"calls": call_volume, "puts": put_volume, "total": total, "put_call_ratio": round(put_volume / call_volume, 2) if call_volume else None},
        "anomaly": anomaly,
        "gex": gex_summary(contracts, spot, rate),
    }


def _leg(action: str, contract: Contract, delta: float, spot: float, rate: float, roll_deltas: list[float] | None = None) -> dict[str, Any]:
    premium = contract.mid
    leg: dict[str, Any] = {
        "action": action,
        "contract": contract.symbol,
        "type": contract.kind,
        "expiry": datetime.fromtimestamp(contract.expiration, ZoneInfo("UTC")).strftime("%Y-%m-%d"),
        "dte": contract.dte,
        "strike": contract.strike,
        "mid": round(premium, 2),
        "delta": round(delta, 2),
        "iv_pct": round(contract.iv * 100, 1),
        "open_interest": contract.open_interest,
    }
    if action in ("buy-call", "buy-put"):
        leg["breakeven"] = round(contract.strike + premium if contract.kind == "call" else contract.strike - premium, 2)
        leg["max_loss_per_contract"] = round(premium * 100, 2)
    elif action == "cash-secured-put":
        collateral = contract.strike * 100
        leg["breakeven"] = round(contract.strike - premium, 2)
        leg["collateral_per_contract"] = round(collateral, 2)
        leg["annualized_yield_pct"] = round(premium * 100 / collateral * 365 / max(contract.dte, 1) * 100, 1)
        leg["roll_lines"] = {
            f"delta_{target:.2f}": round(spot_for_put_delta(contract.strike, contract.dte / 365, rate, contract.iv, target), 2)
            for target in (roll_deltas or [])
        }
    elif action == "covered-call":
        leg["called_away_above"] = contract.strike
        leg["static_yield_pct"] = round(premium / spot * 100, 2)
    return leg


def build_playbook(
    cache: ChainCache, ticker: str, snapshot: dict, rate: float, settings: dict, holdings: dict[str, float], earnings_epoch: int | None = None
) -> dict[str, Any]:
    spot = cache.spot(ticker) or snapshot["close"]
    trend = snapshot["trend"]
    aggressive_cfg, conservative_cfg = settings["aggressive"], settings["conservative"]
    expirations = cache.expirations(ticker)

    def choose(cfg: dict) -> int | None:
        max_dte = min(cfg["max_dte"], settings.get("chain_max_dte", cfg["max_dte"]))
        window = expirations_in_window(expirations, cache.now_epoch, min_dte=cfg["min_dte"], max_dte=max_dte)
        liquidity = {expiration: sum(contract.open_interest for contract in cache.contracts(ticker, expiration)) for expiration in window}
        return pick_expiration(expirations, cache.now_epoch, min_dte=cfg["min_dte"], max_dte=max_dte, target_dte=cfg["target_dte"], open_interest=liquidity)

    aggressive_exp, conservative_exp = choose(aggressive_cfg), choose(conservative_cfg)
    strict = {"min_open_interest": settings["min_open_interest"], "max_spread_pct": settings["max_spread_pct"]}
    relaxed = {"min_open_interest": max(1, settings["min_open_interest"] // 5), "max_spread_pct": settings["max_spread_pct"] * 2.5, "max_delta_gap": 0.15}
    shares = holdings.get(ticker, 0)
    thin: list[str] = []

    csp_range = tuple(conservative_cfg.get("csp_delta_range", (0.18, 0.35)))

    def select(expiration: int | None, kind: str, target: float, delta_range: tuple[float, float] | None = None) -> tuple[Contract, float] | None:
        if expiration is None:
            return None
        pool = [contract for contract in cache.contracts(ticker, expiration) if contract.kind == kind]
        picked = pick_by_delta(pool, spot, rate, target, delta_range=delta_range, **strict)
        if picked is None:
            picked = pick_by_delta(pool, spot, rate, target, delta_range=delta_range, **relaxed)
            if picked is not None:
                thin.append(picked[0].symbol)
        return picked

    aggressive: list[dict] = []
    conservative: list[dict] = []
    notes: list[str] = []
    if trend == "bullish":
        picked = select(aggressive_exp, "call", aggressive_cfg["target_abs_delta"])
        if picked:
            aggressive.append(_leg("buy-call", *picked, spot, rate))
        picked = select(conservative_exp, "put", conservative_cfg["csp_abs_delta"], csp_range)
        if picked:
            conservative.append(_leg("cash-secured-put", *picked, spot, rate, settings["roll_deltas"]))
        if shares >= 100:
            picked = select(conservative_exp, "call", conservative_cfg["covered_call_abs_delta"])
            if picked:
                conservative.append(_leg("covered-call", *picked, spot, rate))
    elif trend == "bearish":
        picked = select(aggressive_exp, "put", aggressive_cfg["target_abs_delta"])
        if picked:
            aggressive.append(_leg("buy-put", *picked, spot, rate))
        picked = select(conservative_exp, "put", conservative_cfg["otm_put_abs_delta"])
        if picked:
            conservative.append(_leg("buy-put", *picked, spot, rate))
        notes.append("偏空不卖裸 Call。")
    else:
        notes.append("震荡：不做激进方向单。")
        if shares >= 100:
            picked = select(conservative_exp, "call", conservative_cfg["covered_call_abs_delta"])
            if picked:
                conservative.append(_leg("covered-call", *picked, spot, rate))
        else:
            picked = select(conservative_exp, "put", conservative_cfg["csp_abs_delta"], csp_range)
            if picked:
                conservative.append(_leg("cash-secured-put", *picked, spot, rate, settings["roll_deltas"]))
    for leg in aggressive + conservative:
        leg["liquidity"] = "thin" if leg["contract"] in thin else "ok"
        if earnings_epoch and cache.now_epoch <= earnings_epoch <= datetime.strptime(leg["expiry"], "%Y-%m-%d").replace(tzinfo=EASTERN).timestamp() + 16 * 3600:
            leg["earnings_before_expiry"] = datetime.fromtimestamp(earnings_epoch, EASTERN).strftime("%Y-%m-%d")
    if thin:
        notes.append("标记 thin 的合约 OI 低或价差宽，挂限价单。")
    if not aggressive and not conservative:
        notes.append("没有满足 OI/价差过滤的合约。")
    return {"ticker": ticker, "trend": trend, "spot": round(spot, 2), "shares_held": shares, "aggressive": aggressive, "conservative": conservative, "notes": notes}


def stock_plan(snapshot: dict, gex: dict | None) -> dict[str, Any]:
    """Rule-based price levels from SMAs, 20-day range and GEX walls; not an order."""
    sma = {int(key): value for key, value in snapshot["sma"].items() if value is not None}
    trend = snapshot["trend"]
    put_wall = (gex or {}).get("put_wall")
    call_wall = (gex or {}).get("call_wall")
    if trend == "bullish":
        upper = max((value for value in (sma.get(5), sma.get(10)) if value), default=None)
        zone = sorted(value for value in (sma.get(20), upper) if value)
        stop = sma.get(50)
        plan = {"bias": "偏多", "action": "回踩分批买入", "entry_zone": [round(zone[0], 2), round(zone[-1], 2)] if zone else None, "invalidate_below": round(stop, 2) if stop else None, "target": call_wall or snapshot.get("high_20d")}
    elif trend == "bearish":
        plan = {"bias": "偏空", "action": "不抄底 / 反弹减仓", "reclaim_level": round(sma[20], 2) if sma.get(20) else None, "support": put_wall or snapshot.get("low_20d")}
    else:
        plan = {"bias": "震荡", "action": "区间低吸高抛或观望", "range": [snapshot.get("low_20d"), snapshot.get("high_20d")], "pivot": round(sma[50], 2) if sma.get(50) else None}
    plan["put_wall"], plan["call_wall"] = put_wall, call_wall
    return plan


def _collect_tickers(config: dict) -> list[str]:
    tickers: list[str] = list(config["indices"]) + list(config["magnificent_seven"])
    for sector in config["sectors"].values():
        if sector.get("etf"):
            tickers.append(sector["etf"])
        tickers.extend(sector["members"])
    tickers.extend(config["options"].get("focus", []))
    tickers.extend(holding["ticker"] for holding in config.get("holdings", []))
    return list(dict.fromkeys(tickers))


def run_scan(
    config: dict,
    *,
    as_of: pd.Timestamp,
    cache_dir: Path,
    client: YahooClient | None = None,
    skip_options: bool = False,
    skip_events: bool = False,
    workers: int | None = None,
) -> dict[str, Any]:
    client = client or YahooClient()
    now_epoch = int(time.time())
    warnings: list[str] = []
    windows = config.get("sma_windows", [5, 10, 20, 30, 50, 100])
    settings = config["options"]
    leader_days, sector_days = config.get("leader_return_days", 5), config.get("sector_return_days", 20)
    history_workers = workers or settings.get("quote_workers", 8)
    option_workers = workers or settings.get("option_workers", 6)
    tickers = _collect_tickers(config)
    macro_symbols = list(config.get("macro", {}))
    rate_symbol = config.get("risk_free_symbol")
    all_symbols = list(dict.fromkeys(tickers + macro_symbols + ([rate_symbol] if rate_symbol else [])))

    def fetch(symbol: str) -> tuple[str, pd.DataFrame | None, str | None]:
        try:
            frame = client.history(symbol, range_="1y")
            frame = frame[frame["Date"] <= as_of].reset_index(drop=True)
            if len(frame) < 25:
                return symbol, None, f"{symbol}: only {len(frame)} daily bars"
            return symbol, frame, None
        except Exception as exc:
            return symbol, None, f"{symbol}: {type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=history_workers) as pool:
        fetched = list(pool.map(fetch, all_symbols))
    frames: dict[str, pd.DataFrame] = {}
    for symbol, frame, error in fetched:
        if error:
            warnings.append(error)
        elif frame is not None:
            frames[symbol] = frame

    snapshots = {symbol: trend_snapshot(frames[symbol], windows, (leader_days, sector_days)) for symbol in tickers if symbol in frames}
    macro = {symbol: {**config["macro"][symbol], **macro_snapshot(frames[symbol], config["macro"][symbol]["unit"])} for symbol in macro_symbols if symbol in frames}
    rate = DEFAULT_RATE
    if rate_symbol in frames:
        rate = float(frames[rate_symbol]["Close"].iloc[-1]) / 100
    data_end = max((snapshot["date"] for snapshot in snapshots.values()), default=None)

    indices = {symbol: {"name": name, **snapshots[symbol]} for symbol, name in config["indices"].items() if symbol in snapshots}
    mag7 = {symbol: snapshots[symbol] for symbol in config["magnificent_seven"] if symbol in snapshots}
    sectors = rank_sectors(config["sectors"], snapshots, settings.get("leaders_per_sector", 1), sector_days=sector_days, leader_days=leader_days)

    quote_symbols = list(dict.fromkeys([t for t in tickers if not t.startswith("^")] + config["large_cap_earnings"].get("extra_tickers", [])))
    quotes: dict[str, dict] = {}
    try:
        quotes = client.quotes(quote_symbols)
    except Exception as exc:
        warnings.append(f"quote batch unavailable: {type(exc).__name__}: {exc}")

    cache = ChainCache(client, now_epoch)
    events: dict[str, Any] = {"status": "skipped"}
    if not skip_events:
        events = build_earnings(quotes, as_of, config, now_epoch, lambda symbol, epoch: straddle_move(cache, symbol, epoch))

    options_section: dict[str, Any] = {"status": "skipped"}
    playbooks: list[dict] = []
    holdings = {holding["ticker"]: float(holding.get("shares", 0)) for holding in config.get("holdings", [])}
    if not skip_options:
        min_cap = settings.get("watchlist_min_market_cap", 2e10)
        watch = []
        if settings.get("include_sector_symbols", True):
            watch = [t for sector in config["sectors"].values() for t in sector["members"] if (quotes.get(t, {}).get("marketCap") or 0) > min_cap]
        option_tickers = list(dict.fromkeys(settings.get("focus", []) + watch + list(holdings)))
        history_path = cache_dir / "options-volume-history.json"
        history = _load_volume_history(history_path)
        as_of_label = data_end or as_of.strftime("%Y-%m-%d")

        def scan_one(symbol: str) -> tuple[str, dict | None, str | None]:
            try:
                return symbol, near_term_options(cache, symbol, rate, settings, history, as_of_label), None
            except Exception as exc:
                return symbol, None, f"options {symbol}: {type(exc).__name__}: {exc}"

        with ThreadPoolExecutor(max_workers=option_workers) as pool:
            scanned = list(pool.map(scan_one, option_tickers))
        per_ticker = {}
        for symbol, result, error in scanned:
            if error:
                warnings.append(error)
            elif result is not None:
                per_ticker[symbol] = result
        cache_dir.mkdir(parents=True, exist_ok=True)
        history_path.write_text(json.dumps(history, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        options_section = {"status": "live-snapshot", "tickers": per_ticker, "risk_free_rate": round(rate, 4)}

        leader_rows = []
        for sector in sectors[: settings.get("playbook_top_sectors", 5)]:
            for leader in sector["leaders"]:
                leader_rows.append((sector, leader))
        leader_rows.extend(({"name": "持仓", "key": "holdings", "rank": None}, ticker) for ticker in holdings if ticker in snapshots)

        def play(item: tuple[dict, str]) -> dict | None:
            sector, ticker = item
            if ticker not in snapshots:
                return None
            try:
                quote = quotes.get(ticker, {})
                earnings_epoch = quote.get("earningsTimestamp") or quote.get("earningsTimestampStart")
                book = build_playbook(cache, ticker, snapshots[ticker], rate, settings, holdings, int(earnings_epoch) if earnings_epoch else None)
            except Exception as exc:
                warnings.append(f"playbook {ticker}: {type(exc).__name__}: {exc}")
                book = {"ticker": ticker, "trend": snapshots[ticker]["trend"], "aggressive": [], "conservative": [], "notes": ["期权链不可用。"]}
            book.update(
                {
                    "sector": sector["name"],
                    "sector_rank": sector.get("rank"),
                    "ret_leader": snapshots[ticker].get(f"ret_{leader_days}d"),
                    "ret_sector": snapshots[ticker].get(f"ret_{sector_days}d"),
                    "stock_plan": stock_plan(snapshots[ticker], per_ticker.get(ticker, {}).get("gex")),
                }
            )
            return book

        with ThreadPoolExecutor(max_workers=option_workers) as pool:
            playbooks = [book for book in pool.map(play, leader_rows) if book]

    return {
        "scan": {"id": SCAN_ID, "version": SCAN_VERSION, "generated_at_utc": datetime.fromtimestamp(now_epoch, ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "as_of": as_of.strftime("%Y-%m-%d"),
        "lookbacks": {"leader_return_days": leader_days, "sector_return_days": sector_days, "megacap_label": config.get("megacap_label", "七巨头")},
        "data_end": data_end,
        "regime": market_regime(indices, macro),
        "indices": indices,
        "magnificent_seven": mag7,
        "macro": macro,
        "events": events,
        "sectors": sectors,
        "options": options_section,
        "playbooks": playbooks,
        "warnings": warnings,
        "disclaimer": "规则化研究输出，不构成投资建议；GEX 为 Yahoo 期权链 + Black-Scholes 估算，不代表做市商真实持仓；软件不下单。",
    }
