from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_PLAYBOOK: dict[str, Any] = {
    "playbook_top_sectors": 5,
    "aggressive": {"min_dte": 21, "max_dte": 45, "target_dte": 30, "target_abs_delta": 0.5},
    "conservative": {"min_dte": 25, "max_dte": 45, "target_dte": 35, "csp_abs_delta": 0.3, "csp_delta_range": [0.18, 0.35], "otm_put_abs_delta": 0.25, "covered_call_abs_delta": 0.25},
    "roll_deltas": [0.30, 0.35, 0.40, 0.50],
    "min_open_interest": 100,
    "max_spread_pct": 0.25,
}


def normalize_universe(raw: dict[str, Any], holdings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Map the user-facing universe file onto the settings consumed by ``run_scan``."""
    lookbacks = raw.get("lookbacks", {})
    monitor = raw.get("options_monitor", {})
    earnings = raw.get("large_cap_earnings", {})
    events = raw.get("events", {})
    strength = raw.get("strength", {})
    playbook = {**DEFAULT_PLAYBOOK, **raw.get("playbooks", {})}
    macro = {}
    for item in raw.get("macro", []):
        macro[item["symbol"]] = {"name": item.get("label", item["symbol"]), "unit": "yield" if item.get("change_mode") == "basis-points" else "usd"}
    indices = {item["symbol"]: item.get("label", item["symbol"]) for item in raw.get("indices", []) + raw.get("market_lenses", [])}
    sectors = {
        item["id"]: {
            "name": item.get("label", item["id"]),
            "etf": item.get("etf"),
            "members": list(item.get("symbols", [])),
            "min_market_cap": float(item.get("minimum_market_cap_usd", 0)),
        }
        for item in raw.get("sectors", [])
    }
    impact_map = {
        ticker: {"theme": value.get("theme"), "linked": value.get("related", value.get("linked", []))}
        for ticker, value in earnings.get("impact_map", {}).items()
    }
    return {
        "indices": indices,
        "magnificent_seven": list(raw.get("megacap", {}).get("symbols", [])),
        "megacap_label": raw.get("megacap", {}).get("label", "七巨头"),
        "macro": macro,
        "risk_free_symbol": raw.get("risk_free_symbol", "^IRX"),
        "sma_windows": lookbacks.get("sma_windows", [5, 10, 20, 30, 50, 100]),
        "leader_return_days": int(lookbacks.get("leader_return_days", 5)),
        "sector_return_days": int(lookbacks.get("sector_return_days", 20)),
        "sectors": sectors,
        "options": {
            **playbook,
            "focus": list(monitor.get("symbols", [])),
            "include_sector_symbols": bool(monitor.get("include_sector_symbols", True)),
            "watchlist_min_market_cap": float(monitor.get("minimum_market_cap_usd", 2e10)),
            "chain_max_dte": int(monitor.get("chain_max_dte", 60)),
            "near_term_max_dte": int(monitor.get("near_term_days", 30)),
            "near_term_max_expirations": int(monitor.get("near_term_max_expirations", 3)),
            "anomaly_multiple": float(monitor.get("volume_spike_multiple", 2.0)),
            "anomaly_min_baseline_days": int(monitor.get("minimum_baseline_days", 3)),
            "history_days": int(monitor.get("history_days", 20)),
            "quote_workers": int(monitor.get("market_cap_workers", 8)),
            "option_workers": int(monitor.get("option_workers", 6)),
            "leaders_per_sector": int(lookbacks.get("leaders_per_sector", 2)),
        },
        "holdings": holdings or [],
        "large_cap_earnings": {
            "min_market_cap": float(earnings.get("minimum_market_cap_usd", 3e11)),
            "impact_map": impact_map,
            "extra_tickers": list(impact_map),
        },
        "events": {
            "lookahead_days": int(events.get("lookahead_days", 30)),
            "manual": events.get("manual", []),
            "macro": events.get("macro", []),
            "macro_lookback_days": int(events.get("macro_lookback_days", 3)),
        },
        "strength": {
            "min_days_above_sma5": int(strength.get("min_days_above_sma5", 2)),
            "require_close_above_sma20": bool(strength.get("require_close_above_sma20", True)),
            "min_score": int(strength.get("min_score", 2)),
            "require_structure": bool(strength.get("require_structure", True)),
            "surge_lookback_days": int(strength.get("surge_lookback_days", 10)),
            "surge_min_return_pct": float(strength.get("surge_min_return_pct", 3.0)),
            "surge_volume_multiple": float(strength.get("surge_volume_multiple", 1.5)),
            "news_lookback_days": int(strength.get("news_lookback_days", 7)),
        },
    }


def holdings_from_profile(profile: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"ticker": item["symbol"], "shares": float(item.get("shares", 0))} for item in profile.get("symbols", []) if float(item.get("shares", 0)) > 0]


def load_universe(path: Path, profile_path: Path | None = None) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    holdings = None
    if profile_path is not None:
        holdings = holdings_from_profile(json.loads(profile_path.read_text(encoding="utf-8")))
    return normalize_universe(raw, holdings)
