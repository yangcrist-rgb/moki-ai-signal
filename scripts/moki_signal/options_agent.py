from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .market_scan import EASTERN, ChainCache
from .options_math import (
    Contract,
    bs_delta,
    bs_price,
    bs_theta_per_day,
    expirations_in_window,
    gex_by_strike,
    gex_summary,
    spot_for_put_delta,
    usable_iv,
)
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
    """Per-trade cash cap; a short put can never reserve more than the cash actually held."""
    if symbol_cfg.get("per_trade_cash_limit_usd"):
        limit = float(symbol_cfg["per_trade_cash_limit_usd"])
    elif profile.get("account_size_usd"):
        limit = float(profile["account_size_usd"]) * float(profile.get("risk", {}).get("default_per_trade_cash_pct", 0.1))
    else:
        limit = float(profile.get("cash_usd") or 0)
    if profile.get("cash_usd") is not None:
        limit = min(limit, float(profile["cash_usd"]))
    return limit


def extended_hours(quote: dict) -> dict[str, Any] | None:
    state = str(quote.get("marketState") or "")
    if state == "REGULAR":
        return None
    price, change, session = None, None, "post-market"
    if state.startswith("PRE") and quote.get("preMarketPrice"):
        price, change, session = quote.get("preMarketPrice"), quote.get("preMarketChangePercent"), "pre-market"
    elif quote.get("postMarketPrice"):
        price, change = quote.get("postMarketPrice"), quote.get("postMarketChangePercent")
    if not price:
        return None
    return {"session": session, "price": round(float(price), 2), "change_pct": round(float(change), 2) if change is not None else None}


def long_call_signals(position: dict[str, Any], trend_state: str) -> list[str]:
    """Management prompts for a long option; ordered by urgency."""
    signals = []
    estimate = position.get("extended_estimate") or {}
    pnl = estimate.get("pnl_pct", position.get("pnl_pct"))
    dte, delta = position["dte"], position.get("delta")
    if dte <= 60:
        signals.append("≤60 天：Theta 加速衰减，平仓或滚动到更远月份")
    elif dte <= 90:
        signals.append("≤90 天：提前规划滚动，避免进入 60 天内加速衰减区")
    if pnl is not None and pnl >= 100:
        signals.append("利润翻倍：卖一半收回本金，或上移行权价滚动锁定利润")
    elif pnl is not None and pnl >= 50:
        signals.append("盈利 ≥50%：设移动止盈（如权利金回撤到成本 +25% 离场）")
    elif pnl is not None and pnl <= -50:
        signals.append("亏损 ≥50%：复核逻辑；正股跌破 SMA50 且未收复则按纪律止损")
    if position["type"] == "call" and trend_state == "bearish":
        signals.append("正股明确空头：Call 与趋势相反，降低仓位")
    if position.get("earnings_before_expiry"):
        signals.append(f"到期前财报 {position['earnings_before_expiry']}：财报后 IV 回落会压低权利金")
    if delta is not None and delta < 0.3:
        signals.append("Delta < 0.30：偏彩票型，需正股大涨才能回本")
    if not signals:
        signals.append("持有：无触发条件")
    return signals


def evaluate_option_position(
    cache: ChainCache, position: dict, spot: float, rate: float, trend_state: str, earnings_epoch: int | None, ext: dict | None
) -> dict[str, Any]:
    kind = str(position.get("type", "call")).lower()
    strike = float(position["strike"])
    contracts = int(position.get("contracts", 1))
    cost = float(position["cost"])
    expiry = str(position["expiry"])
    symbol = position["symbol"]
    match = next((e for e in cache.expirations(symbol) if datetime.fromtimestamp(e, ZoneInfo("UTC")).strftime("%Y-%m-%d") == expiry), None)
    row: dict[str, Any] = {"symbol": symbol, "type": kind, "expiry": expiry, "strike": strike, "contracts": contracts, "cost": cost, "cost_total": round(cost * 100 * contracts, 2)}
    contract = None
    if match is not None:
        contract = next((c for c in cache.contracts(symbol, match) if c.kind == kind and c.strike == strike), None)
    expiry_epoch = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=EASTERN).timestamp() + 16 * 3600
    row["dte"] = max(0, math.ceil((expiry_epoch - cache.now_epoch) / 86400))
    if contract is None:
        row.update({"status": "contract-not-found", "signals": ["Yahoo 找不到该合约，请核对到期日与行权价"]})
        return row
    mid = contract.mid
    years = row["dte"] / 365
    value = mid * 100 * contracts
    intrinsic = max(0.0, spot - strike) if kind == "call" else max(0.0, strike - spot)
    row.update(
        {
            "status": "ok",
            "contract": contract.symbol,
            "bid": contract.bid,
            "ask": contract.ask,
            "mid": round(mid, 2),
            "value": round(value, 2),
            "pnl": round(value - row["cost_total"], 2),
            "pnl_pct": round((mid / cost - 1) * 100, 1) if cost else None,
            "intrinsic": round(intrinsic, 2),
            "time_value": round(mid - intrinsic, 2),
            "breakeven_at_expiry": round(strike + cost if kind == "call" else strike - cost, 2),
            "open_interest": contract.open_interest,
        }
    )
    if usable_iv(contract):
        delta = bs_delta(kind, spot, strike, years, rate, contract.iv)
        theta = bs_theta_per_day(kind, spot, strike, years, rate, contract.iv)
        row.update(
            {
                "iv_pct": round(contract.iv * 100, 1),
                "delta": round(delta, 3),
                "theta_per_day_usd": round(theta * 100 * contracts, 2),
                "delta_dollars": round(delta * 100 * contracts * spot, 2),
            }
        )
        if ext:
            shift = bs_price(kind, ext["price"], strike, years, rate, contract.iv) - bs_price(kind, spot, strike, years, rate, contract.iv)
            estimate = max(0.0, mid + shift)
            row["extended_estimate"] = {
                "spot": ext["price"],
                "mid": round(estimate, 2),
                "pnl": round(estimate * 100 * contracts - row["cost_total"], 2),
                "pnl_pct": round((estimate / cost - 1) * 100, 1) if cost else None,
            }
    if earnings_epoch and cache.now_epoch <= earnings_epoch <= expiry_epoch:
        row["earnings_before_expiry"] = datetime.fromtimestamp(earnings_epoch, EASTERN).strftime("%Y-%m-%d")
    row["signals"] = long_call_signals(row, trend_state) if kind == "call" else ["买入 Put 持仓：按同样的时间与盈亏规则管理"]
    return row


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


def evaluate_symbol(
    cache: ChainCache, client: YahooClient, profile: dict, symbol_cfg: dict, rate: float, quote: dict, positions: list[dict] | None = None
) -> dict[str, Any]:
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
    window_contracts = [contract for expiration in window for contract in cache.contracts(symbol, expiration)]

    def candidates(kind: str, oi: int, spread: float) -> list[Contract]:
        picked = []
        for contract in window_contracts:
            if contract.kind != kind or not _liquid(contract, oi, spread):
                continue
            if (kind == "put" and contract.strike >= spot) or (kind == "call" and contract.strike <= spot):
                continue
            if low <= abs(bs_delta(kind, spot, contract.strike, contract.dte / 365, rate, contract.iv)) <= high:
                picked.append(contract)
        return picked

    thin: set[str] = set()

    def with_fallback(kind: str) -> list[Contract]:
        strict = candidates(kind, min_oi, max_spread)
        if strict:
            return strict
        relaxed = candidates(kind, max(1, min_oi // 4), max_spread * 2)
        thin.update(contract.symbol for contract in relaxed)
        return relaxed

    puts = with_fallback("put")
    calls = with_fallback("call") if shares >= 100 else []

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

    for row in sell_puts + covered_calls:
        row["liquidity"] = "thin" if row["contract"] in thin else "ok"
    if shares >= 100 and not covered_calls:
        notes.append("没有满足 DTE/Delta/OI 过滤的 Covered Call。")
    ext = extended_hours(quote)
    stock_position = None
    if shares > 0:
        basis = symbol_cfg.get("cost_basis")
        value = shares * spot
        stock_position = {"shares": shares, "cost_basis": basis, "value": round(value, 2), "delta_dollars": round(value, 2)}
        if basis:
            stock_position.update({"pnl": round((spot - float(basis)) * shares, 2), "pnl_pct": round((spot / float(basis) - 1) * 100, 2)})
            if ext:
                stock_position["extended_pnl"] = round((ext["price"] - float(basis)) * shares, 2)
    option_positions = [evaluate_option_position(cache, item, spot, rate, trend["state"], earnings_epoch, ext) for item in positions or []]

    return {
        "symbol": symbol,
        "role": symbol_cfg.get("role", "watch"),
        "extended_hours": ext,
        "stock_position": stock_position,
        "option_positions": option_positions,
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
    positions = profile.get("option_positions", [])
    symbols = list(profile.get("symbols", []))
    known = {item["symbol"] for item in symbols}
    for item in positions:
        if item["symbol"] not in known:
            symbols.append({"symbol": item["symbol"], "role": "holding", "shares": 0})
            known.add(item["symbol"])
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
            mine = [position for position in positions if position["symbol"] == item["symbol"]]
            return evaluate_symbol(cache, client, profile, item, rate, quotes.get(item["symbol"], {}), mine)
        except Exception as exc:
            warnings.append(f"{item['symbol']}: {type(exc).__name__}: {exc}")
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = [row for row in pool.map(one, symbols) if row]
    return {
        "portfolio": portfolio_summary(profile, results),
        "agent": {"id": AGENT_ID, "version": AGENT_VERSION, "generated_at_utc": datetime.fromtimestamp(now_epoch, ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "profile": profile.get("name"),
        "account_size_usd": profile.get("account_size_usd"),
        "risk": profile.get("risk", {}),
        "risk_free_rate": round(rate, 4),
        "symbols": results,
        "warnings": warnings,
        "disclaimer": "教育与研究用途；Delta/Gamma 为基于 IV 快照的 Black-Scholes 估算，不是交易所 Greeks；不构成投资建议，不下单。",
    }


def portfolio_summary(profile: dict, results: list[dict]) -> dict[str, Any] | None:
    has_positions = any(row["stock_position"] or row["option_positions"] for row in results)
    if not has_positions and profile.get("cash_usd") is None:
        return None
    cash = float(profile.get("cash_usd") or 0)
    stock_value = sum(row["stock_position"]["value"] for row in results if row["stock_position"])
    option_rows = [p for row in results for p in row["option_positions"] if p.get("status") == "ok"]
    option_value = sum(p["value"] for p in option_rows)
    cost = sum(p["cost_total"] for p in option_rows) + sum(
        row["stock_position"]["shares"] * float(row["stock_position"]["cost_basis"]) for row in results if row["stock_position"] and row["stock_position"].get("cost_basis")
    )
    equity = cash + stock_value + option_value
    exposure = []
    for row in results:
        dollars = (row["stock_position"] or {}).get("delta_dollars", 0) + sum(p.get("delta_dollars", 0) for p in row["option_positions"])
        if dollars:
            exposure.append({"symbol": row["symbol"], "delta_dollars": round(dollars, 2), "pct_of_equity": round(dollars / equity * 100, 1) if equity else None})
    exposure.sort(key=lambda item: -item["delta_dollars"])
    total_delta = sum(item["delta_dollars"] for item in exposure)
    return {
        "cash": round(cash, 2),
        "stock_value": round(stock_value, 2),
        "option_value": round(option_value, 2),
        "equity": round(equity, 2),
        "cash_pct": round(cash / equity * 100, 1) if equity else None,
        "cost_basis": round(cost, 2),
        "unrealized_pnl": round(stock_value + option_value - cost, 2),
        "theta_per_day_usd": round(sum(p.get("theta_per_day_usd", 0) for p in option_rows), 2),
        "delta_dollars": round(total_delta, 2),
        "delta_pct_of_equity": round(total_delta / equity * 100, 1) if equity else None,
        "exposure": exposure,
    }


def _money(value: float | None) -> str:
    if value is None:
        return "-"
    return f"-${abs(value):,.0f}" if value < 0 else f"${value:,.0f}"


def _portfolio_section(result: dict[str, Any]) -> list[str]:
    summary = result.get("portfolio")
    if not summary:
        return []
    out = [
        "## 账户概览",
        "",
        "| 现金 | 正股市值 | 期权市值 | 总权益 | 现金占比 | 未实现盈亏 | 每日 Theta | Delta 美元敞口 |",
        "|---|---|---|---|---|---|---|---|",
        f"| {_money(summary['cash'])} | {_money(summary['stock_value'])} | {_money(summary['option_value'])} | {_money(summary['equity'])} | {summary['cash_pct']}% | "
        f"{_money(summary['unrealized_pnl'])} | {_money(summary['theta_per_day_usd'])} | {_money(summary['delta_dollars'])}（{summary['delta_pct_of_equity']}% 权益） |",
        "",
        "| 代码 | Delta 美元敞口 | 占权益 |",
        "|---|---|---|",
    ]
    out += [f"| {item['symbol']} | {_money(item['delta_dollars'])} | {item['pct_of_equity']}% |" for item in summary["exposure"]]
    rows = []
    for row in result["symbols"]:
        ext = row.get("extended_hours")
        ext_label = f"{ext['price']:,.2f}（{ext['change_pct']:+.2f}%）" if ext and ext.get("change_pct") is not None else "-"
        stock = row.get("stock_position")
        if stock:
            rows.append(
                f"| {row['symbol']} | 正股 {stock['shares']:,.0f} 股 | {stock.get('cost_basis') or '-'} | {row['spot']:,.2f} | {ext_label} | - | {_money(stock['value'])} | "
                f"{_money(stock.get('pnl'))}（{stock.get('pnl_pct', 0):+.1f}%） | {_money(stock.get('extended_pnl'))} | - | - | - | {'可卖 Covered Call，见下文' if row['covered_call_candidates'] else '持有'} |"
            )
        for pos in row["option_positions"]:
            label = f"{pos['expiry']} {pos['strike']:g} {pos['type'].upper()} ×{pos['contracts']}"
            if pos.get("status") != "ok":
                rows.append(f"| {row['symbol']} | {label} | {pos['cost']} | {row['spot']:,.2f} | {ext_label} | - | - | - | - | - | - | {pos['dte']} | {'；'.join(pos['signals'])} |")
                continue
            est = pos.get("extended_estimate")
            est_label = f"{_money(est['pnl'])}（{est['pnl_pct']:+.1f}%）" if est else "-"
            rows.append(
                f"| {row['symbol']} | {label} | {pos['cost']} | {row['spot']:,.2f} | {ext_label} | {pos['mid']:.2f} | {_money(pos['value'])} | "
                f"{_money(pos['pnl'])}（{pos['pnl_pct']:+.1f}%） | {est_label} | {pos.get('delta', '-')} | {_money(pos.get('theta_per_day_usd'))} | {pos['dte']} | {'；'.join(pos['signals'])} |"
            )
    if rows:
        out += [
            "",
            "## 持仓管理",
            "",
            "| 代码 | 持仓 | 成本 | 收盘价 | 盘前/盘后 | 期权中间价 | 市值 | 盈亏（收盘） | 盈亏（按盘前/盘后估） | Delta | 每日 Theta | 剩余天数 | 管理提示 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ] + rows
        out += ["", "期权中间价取最近买卖报价；盘前/盘后估值按相同 IV 用 Black-Scholes 平移正股价得到，不含 IV 变化（财报后 IV 通常回落）。"]
    return out + [""]


def render_agent_markdown(result: dict[str, Any]) -> str:
    risk = result["risk"]
    account = result.get("account_size_usd")
    out = [
        f"# 期权辅助决策：{result['profile']}",
        "",
        (f"账户 ${account:,}；" if account else "") + f"卖 Put 窗口 {risk.get('sell_put_min_dte', 25)}–{risk.get('sell_put_max_dte', 45)} DTE，"
        f"|Delta| {risk.get('target_delta_low', 0.18)}–{risk.get('target_delta_high', 0.35)}，OI ≥ {risk.get('min_open_interest', 200)}；无风险利率 {result['risk_free_rate'] * 100:.2f}%。",
        "",
    ]
    out += _portfolio_section(result)
    out += [
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
            flags += "；流动性偏薄" if c.get("liquidity") == "thin" else ""
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
        out += ["", "## Covered Call 候选", "", "| 代码 | 到期 | 行权价 | 权利金 | Delta | OI | 张数 | 静态收益 | 被行权收益 | 止盈回补价 60%/80% | 备注 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for row, c in calls:
            flag = "流动性偏薄，挂限价" if c.get("liquidity") == "thin" else "-"
            out.append(f"| {row['symbol']} | {c['expiry']} ({c['dte']}D) | {c['strike']:,.2f} | {c['credit']:.2f} | {c['delta']:.2f} | {c['open_interest']:,} | {c['contracts']} | {c['static_return_pct']:.2f}% | {c['if_called_return_pct']:.2f}% | {c['take_profit_buyback']['60pct']:.2f} / {c['take_profit_buyback']['80pct']:.2f} | {flag} |")
    notes = [f"- {row['symbol']}：{note}" for row in result["symbols"] for note in row["notes"]]
    if notes:
        out += ["", "## 备注", ""] + notes
    if result["warnings"]:
        out += ["", "## 数据警告", ""] + [f"- {item}" for item in result["warnings"]]
    out += ["", f"> {result['disclaimer']}", ""]
    return "\n".join(out)
