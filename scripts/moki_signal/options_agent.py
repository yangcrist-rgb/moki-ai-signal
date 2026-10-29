from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .market_scan import EASTERN, ChainCache
from .options_math import Contract, bs_delta, expirations_in_window, gex_by_strike, gex_summary, spot_for_put_delta, usable_iv
from .yahoo import YahooClient


AGENT_ID = "mcmillan-options-agent"
AGENT_VERSION = "0.1.0"
DEFAULT_RATE = 0.04
MANAGEMENT_DELTAS = {"0.30": "watch", "0.35": "roll-out-and-down", "0.40": "mandatory-defense", "0.50": "assignment-review"}


def long_term_trend(frame: pd.DataFrame) -> dict[str, Any]:
    """20/50/200-day rule: only a clear downtrend blocks short puts."""
    close = frame["Close"].astype(float)
    last = float(close.iloc[-1])
    sma = {window: (float(close.tail(window).mean()) if len(close) >= window else None) for window in (20, 50, 200)}
    sma20, sma50, sma200 = sma[20], sma[50], sma[200]
    if sma20 is None or sma50 is None:
        state = "insufficient-data"
    elif last > sma20 > sma50 and (sma200 is None or last > sma200):
        state = "bullish"
    elif last < sma20 < sma50 and (sma200 is None or last < sma200):
        state = "bearish"
    else:
        state = "transition"
    return {
        "close": round(last, 2),
        "sma20": round(sma20, 2) if sma20 else None,
        "sma50": round(sma50, 2) if sma50 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "state": state,
    }


def cash_limit(profile: dict, symbol_cfg: dict) -> float:
    if symbol_cfg.get("per_trade_cash_limit_usd"):
        return float(symbol_cfg["per_trade_cash_limit_usd"])
    return float(profile.get("account_size_usd", 0)) * float(profile.get("risk", {}).get("default_per_trade_cash_pct", 0.1))


def _expiry_label(contract: Contract) -> str:
    return datetime.fromtimestamp(contract.expiration, ZoneInfo("UTC")).strftime("%Y-%m-%d")


def csp_candidate(contract: Contract, spot: float, rate: float, limit_usd: float, earnings_epoch: int | None) -> dict[str, Any]:
    years = contract.dte / 365
    delta = bs_delta("put", spot, contract.strike, years, rate, contract.iv)
    credit = contract.mid
    collateral = contract.strike * 100
    contracts = int(limit_usd // collateral) if collateral > 0 else 0
    expiry_epoch = datetime.strptime(_expiry_label(contract), "%Y-%m-%d").replace(tzinfo=EASTERN).timestamp() + 16 * 3600
    row = {
        "contract": contract.symbol,
        "expiry": _expiry_label(contract),
        "dte": contract.dte,
        "strike": contract.strike,
        "credit": round(credit, 2),
        "delta": round(delta, 3),
        "iv_pct": round(contract.iv * 100, 1),
        "open_interest": contract.open_interest,
        "spread_pct": round(contract.spread_pct * 100, 1) if contract.spread_pct is not None else None,
        "otm_pct": round((1 - contract.strike / spot) * 100, 2),
        "breakeven": round(contract.strike - credit, 2),
        "return_on_cash_pct": round(credit / contract.strike * 100, 2),
        "annualized_pct": round(credit / contract.strike * 365 / max(contract.dte, 1) * 100, 1),
        "contracts_within_limit": contracts,
        "cash_reserved": round(contracts * collateral, 2),
        "total_credit": round(contracts * credit * 100, 2),
        "take_profit_buyback": {"60pct": round(credit * 0.4, 2), "80pct": round(credit * 0.2, 2)},
        "management_lines": {
            f"delta_{key}": {"spot": round(spot_for_put_delta(contract.strike, years, rate, contract.iv, float(key)), 2), "action": action}
            for key, action in MANAGEMENT_DELTAS.items()
        },
    }
    if earnings_epoch and time.time() <= earnings_epoch <= expiry_epoch:
        row["earnings_before_expiry"] = datetime.fromtimestamp(earnings_epoch, EASTERN).strftime("%Y-%m-%d")
    return row


def covered_call_candidate(contract: Contract, spot: float, rate: float, shares: float) -> dict[str, Any]:
    delta = bs_delta("call", spot, contract.strike, contract.dte / 365, rate, contract.iv)
    credit = contract.mid
    return {
        "contract": contract.symbol,
        "expiry": _expiry_label(contract),
        "dte": contract.dte,
        "strike": contract.strike,
        "credit": round(credit, 2),
        "delta": round(delta, 3),
        "iv_pct": round(contract.iv * 100, 1),
        "open_interest": contract.open_interest,
        "contracts": int(shares // 100),
        "static_return_pct": round(credit / spot * 100, 2),
        "if_called_return_pct": round((contract.strike - spot + credit) / spot * 100, 2),
        "take_profit_buyback": {"60pct": round(credit * 0.4, 2), "80pct": round(credit * 0.2, 2)},
    }


def _liquid(contract: Contract, min_oi: int, max_spread: float) -> bool:
    spread = contract.spread_pct
    return usable_iv(contract) and contract.open_interest >= min_oi and contract.mid > 0 and spread is not None and spread <= max_spread


def wall_clusters(contracts: list[Contract], spot: float, rate: float, top: int = 3) -> dict[str, list[dict]]:
    exposure = gex_by_strike(contracts, spot, rate)
    positive = sorted(((k, v) for k, v in exposure.items() if v > 0), key=lambda kv: -kv[1])[:top]
    negative = sorted(((k, v) for k, v in exposure.items() if v < 0), key=lambda kv: kv[1])[:top]
    return {
        "positive": [{"strike": k, "gex_usd_per_1pct": round(v)} for k, v in positive],
        "negative": [{"strike": k, "gex_usd_per_1pct": round(v)} for k, v in negative],
    }


def evaluate_symbol(cache: ChainCache, client: YahooClient, profile: dict, symbol_cfg: dict, rate: float, quote: dict) -> dict[str, Any]:
    risk = profile.get("risk", {})
    symbol = symbol_cfg["symbol"]
    shares = float(symbol_cfg.get("shares", 0))
    min_oi = int(risk.get("min_open_interest", 200))
    max_spread = float(risk.get("max_spread_pct", 0.25))
    low, high = float(risk.get("target_delta_low", 0.18)), float(risk.get("target_delta_high", 0.35))
    min_dte, max_dte = int(risk.get("sell_put_min_dte", 25)), int(risk.get("sell_put_max_dte", 45))
    near_days = int(risk.get("near_term_days", 30))

    trend = long_term_trend(client.history(symbol, range_="2y"))
    spot = cache.spot(symbol) or trend["close"]
    expirations = cache.expirations(symbol)
    near = [e for e in expirations if math.ceil((e + 16 * 3600 - cache.now_epoch) / 86400) <= near_days]
    near_contracts = [c for e in near for c in cache.contracts(symbol, e)]
    earnings_epoch = quote.get("earningsTimestamp") or quote.get("earningsTimestampStart")
    earnings_epoch = int(earnings_epoch) if earnings_epoch else None
    limit = cash_limit(profile, symbol_cfg)

    window = expirations_in_window(expirations, cache.now_epoch, min_dte=min_dte, max_dte=max_dte)
    puts, calls = [], []
    for expiration in window:
        for contract in cache.contracts(symbol, expiration):
            if not _liquid(contract, min_oi, max_spread):
                continue
            delta = abs(bs_delta(contract.kind, spot, contract.strike, contract.dte / 365, rate, contract.iv))
            if not low <= delta <= high:
                continue
            if contract.kind == "put" and contract.strike < spot:
                puts.append(contract)
            elif contract.kind == "call" and contract.strike > spot:
                calls.append(contract)

    notes: list[str] = []
    sell_puts: list[dict] = []
    if trend["state"] == "bearish":
        notes.append("方向过滤：明确空头趋势，不卖 Put。")
    else:
        rows = [csp_candidate(c, spot, rate, limit, earnings_epoch) for c in puts]
        affordable = [row for row in rows if row["contracts_within_limit"] > 0]
        if rows and not affordable:
            notes.append(f"候选 Put 的单张保证金都超过单笔现金上限 ${limit:,.0f}。")
        sell_puts = sorted(affordable, key=lambda row: -row["annualized_pct"])[:5]
        if not rows:
            notes.append("没有满足 DTE/Delta/OI/价差过滤的 Put。")
    covered_calls: list[dict] = []
    if shares >= 100:
        covered_calls = sorted((covered_call_candidate(c, spot, rate, shares) for c in calls), key=lambda row: -row["static_return_pct"])[:3]

    return {
        "symbol": symbol,
        "role": symbol_cfg.get("role", "watch"),
        "notes_profile": symbol_cfg.get("notes"),
        "shares": shares,
        "spot": round(spot, 2),
        "trend": trend,
        "per_trade_cash_limit_usd": round(limit, 2),
        "earnings_date": datetime.fromtimestamp(earnings_epoch, EASTERN).strftime("%Y-%m-%d") if earnings_epoch else None,
        "earnings_passed": bool(earnings_epoch and earnings_epoch < cache.now_epoch),
        "gex": {**gex_summary(near_contracts, spot, rate), **wall_clusters(near_contracts, spot, rate)},
        "sell_put_candidates": sell_puts,
        "covered_call_candidates": covered_calls,
        "notes": notes,
    }


def run_agent(profile: dict, *, client: YahooClient | None = None, workers: int = 6) -> dict[str, Any]:
    client = client or YahooClient()
    now_epoch = int(time.time())
    cache = ChainCache(client, now_epoch)
    symbols = profile.get("symbols", [])
    warnings: list[str] = []
    rate = DEFAULT_RATE
    try:
        rate = float(client.history("^IRX", range_="1mo")["Close"].iloc[-1]) / 100
    except Exception as exc:
        warnings.append(f"^IRX unavailable, using {DEFAULT_RATE:.2%}: {exc}")
    quotes: dict[str, dict] = {}
    try:
        quotes = client.quotes([item["symbol"] for item in symbols])
    except Exception as exc:
        warnings.append(f"quotes unavailable: {exc}")

    def one(item: dict) -> dict | None:
        try:
            return evaluate_symbol(cache, client, profile, item, rate, quotes.get(item["symbol"], {}))
        except Exception as exc:
            warnings.append(f"{item['symbol']}: {type(exc).__name__}: {exc}")
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = [row for row in pool.map(one, symbols) if row]
    return {
        "agent": {"id": AGENT_ID, "version": AGENT_VERSION, "generated_at_utc": datetime.fromtimestamp(now_epoch, ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "profile": profile.get("name"),
        "account_size_usd": profile.get("account_size_usd"),
        "risk": profile.get("risk", {}),
        "risk_free_rate": round(rate, 4),
        "symbols": results,
        "warnings": warnings,
        "disclaimer": "教育与研究用途；Delta/Gamma 为基于 IV 快照的 Black-Scholes 估算，不是交易所 Greeks；不构成投资建议，不下单。",
    }


def render_agent_markdown(result: dict[str, Any]) -> str:
    risk = result["risk"]
    out = [
        f"# 期权辅助决策：{result['profile']}",
        "",
        f"账户 ${result['account_size_usd']:,}；卖 Put 窗口 {risk.get('sell_put_min_dte', 25)}–{risk.get('sell_put_max_dte', 45)} DTE，"
        f"|Delta| {risk.get('target_delta_low', 0.18)}–{risk.get('target_delta_high', 0.35)}，OI ≥ {risk.get('min_open_interest', 200)}；无风险利率 {result['risk_free_rate'] * 100:.2f}%。",
        "",
        "## 正股趋势与 GEX",
        "",
        "| 代码 | 角色 | 持股 | 现价 | SMA20 | SMA50 | SMA200 | 趋势 | Put 墙 | Call 墙 | Gamma 状态 | 财报 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in result["symbols"]:
        trend, gex = row["trend"], row["gex"]
        out.append(
            f"| {row['symbol']} | {row['role']} | {row['shares']:,.0f} | {row['spot']:,.2f} | {trend['sma20']} | {trend['sma50']} | {trend['sma200']} | {trend['state']} | "
            f"{gex.get('put_wall') or '-'} | {gex.get('call_wall') or '-'} | {gex.get('regime', '-')} | {(row['earnings_date'] or '-') + ('（已过）' if row.get('earnings_passed') else '')} |"
        )
    out += ["", "## 现金担保卖 Put 候选", ""]
    rows = []
    for row in result["symbols"]:
        for c in row["sell_put_candidates"]:
            lines = " / ".join(f"Δ{key[-4:]}@{value['spot']}" for key, value in c["management_lines"].items())
            flags = f"；**到期前财报 {c['earnings_before_expiry']}**" if c.get("earnings_before_expiry") else ""
            rows.append(
                f"| {row['symbol']} | {c['expiry']} ({c['dte']}D) | {c['strike']:,.2f} | {c['credit']:.2f} | {c['delta']:.2f} | {c['otm_pct']:.1f}% | {c['annualized_pct']:.1f}% | "
                f"{c['contracts_within_limit']} 张 / ${c['cash_reserved']:,.0f} | {c['take_profit_buyback']['60pct']:.2f} / {c['take_profit_buyback']['80pct']:.2f} | {lines}{flags} |"
            )
    if rows:
        out += ["| 代码 | 到期 | 行权价 | 权利金 | Delta | 价外 | 年化 | 张数 / 占用现金 | 止盈回补价 60%/80% | 管理线（正股价） |", "|---|---|---|---|---|---|---|---|---|---|"] + rows
        out += ["", "管理线：Δ0.30 观察；Δ0.35 向下向后滚动（优先净收权利金）；Δ0.40 必须防守；Δ0.50 准备接股 / 风险复核。"]
    else:
        out += ["无符合条件的候选。"]
    calls = [(row, c) for row in result["symbols"] for c in row["covered_call_candidates"]]
    if calls:
        out += ["", "## Covered Call 候选", "", "| 代码 | 到期 | 行权价 | 权利金 | Delta | 张数 | 静态收益 | 被行权收益 | 止盈回补价 60%/80% |", "|---|---|---|---|---|---|---|---|---|"]
        for row, c in calls:
            out.append(f"| {row['symbol']} | {c['expiry']} ({c['dte']}D) | {c['strike']:,.2f} | {c['credit']:.2f} | {c['delta']:.2f} | {c['contracts']} | {c['static_return_pct']:.2f}% | {c['if_called_return_pct']:.2f}% | {c['take_profit_buyback']['60pct']:.2f} / {c['take_profit_buyback']['80pct']:.2f} |")
    notes = [f"- {row['symbol']}：{note}" for row in result["symbols"] for note in row["notes"]]
    if notes:
        out += ["", "## 备注", ""] + notes
    if result["warnings"]:
        out += ["", "## 数据警告", ""] + [f"- {item}" for item in result["warnings"]]
    out += ["", f"> {result['disclaimer']}", ""]
    return "\n".join(out)
