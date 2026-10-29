from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from moki_signal.options_agent import cash_limit, csp_candidate, extended_hours, long_call_signals, long_term_trend, portfolio_summary  # noqa: E402
from moki_signal.options_math import Contract, bs_delta, pick_by_delta  # noqa: E402
from moki_signal.universe import holdings_from_profile, load_universe  # noqa: E402


def closes(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Date": pd.bdate_range("2025-01-01", periods=len(values)), "Close": values})


class UniverseTests(unittest.TestCase):
    def test_bundled_universe_maps_to_scan_settings(self) -> None:
        config = load_universe(ROOT / "configs" / "us_market_universe.json")
        self.assertEqual(config["indices"]["SPY"], "标普500")
        self.assertEqual(list(config["indices"]), ["SPY", "QQQ", "DIA", "SOXX", "IGV"])
        self.assertEqual(config["macro"]["^TNX"]["unit"], "yield")
        self.assertEqual(config["macro"]["CL=F"]["unit"], "usd")
        self.assertEqual(config["sectors"]["storage"]["members"][0], "MU")
        self.assertEqual(config["options"]["near_term_max_dte"], 30)
        self.assertEqual(config["options"]["leaders_per_sector"], 2)
        self.assertEqual(config["large_cap_earnings"]["impact_map"]["META"]["linked"][0], "NVDA")
        self.assertEqual(config["large_cap_earnings"]["min_market_cap"], 3e11)

    def test_profile_holdings_require_shares(self) -> None:
        profile = json.loads((ROOT / "configs" / "options_agent_profile.example.json").read_text(encoding="utf-8"))
        self.assertEqual(holdings_from_profile(profile), [])
        profile["symbols"][0]["shares"] = 200
        self.assertEqual(holdings_from_profile(profile), [{"ticker": "INTC", "shares": 200.0}])


class AgentRuleTests(unittest.TestCase):
    def test_trend_uses_200_day_filter(self) -> None:
        self.assertEqual(long_term_trend(closes([100 + i * 0.5 for i in range(260)]))["state"], "bullish")
        self.assertEqual(long_term_trend(closes([300 - i * 0.5 for i in range(260)]))["state"], "bearish")
        rebound = [300 - i for i in range(200)] + [101 + i for i in range(40)]
        self.assertEqual(long_term_trend(closes(rebound))["state"], "transition")

    def test_cash_limit_prefers_symbol_override(self) -> None:
        profile = {"account_size_usd": 30000, "risk": {"default_per_trade_cash_pct": 0.12}}
        self.assertEqual(cash_limit(profile, {"per_trade_cash_limit_usd": 12000}), 12000)
        self.assertAlmostEqual(cash_limit(profile, {}), 3600)

    def test_cash_limit_capped_by_cash_held(self) -> None:
        self.assertEqual(cash_limit({"cash_usd": 13109.25}, {}), 13109.25)
        self.assertEqual(cash_limit({"cash_usd": 5000, "account_size_usd": 100000, "risk": {"default_per_trade_cash_pct": 0.12}}, {}), 5000)

    def test_extended_hours_uses_post_market_overnight(self) -> None:
        quote = {"marketState": "PREPRE", "postMarketPrice": 292.5, "postMarketChangePercent": -9.97, "preMarketPrice": None}
        self.assertEqual(extended_hours(quote), {"session": "post-market", "price": 292.5, "change_pct": -9.97})
        self.assertEqual(extended_hours({"marketState": "PRE", "preMarketPrice": 101.0, "preMarketChangePercent": 1.0, "postMarketPrice": 99.0})["session"], "pre-market")
        self.assertIsNone(extended_hours({"marketState": "REGULAR", "postMarketPrice": 99.0}))

    def test_long_call_signals_prefer_extended_estimate(self) -> None:
        position = {"type": "call", "dte": 450, "pnl_pct": -26.8, "delta": 0.62, "extended_estimate": {"pnl_pct": -50.8}}
        signals = long_call_signals(position, "bearish")
        self.assertTrue(any("亏损 ≥50%" in item for item in signals))
        self.assertTrue(any("明确空头" in item for item in signals))
        self.assertTrue(any("≤60 天" in item for item in long_call_signals({"type": "call", "dte": 40, "pnl_pct": 120, "delta": 0.8}, "bullish")))
        self.assertTrue(any("利润翻倍" in item for item in long_call_signals({"type": "call", "dte": 200, "pnl_pct": 120, "delta": 0.8}, "bullish")))

    def test_portfolio_summary_totals(self) -> None:
        results = [
            {"symbol": "CRDO", "stock_position": {"shares": 100, "cost_basis": 171.5, "value": 17369.0, "delta_dollars": 17369.0}, "option_positions": []},
            {"symbol": "AMD", "stock_position": None, "option_positions": [{"status": "ok", "value": 5100.0, "cost_total": 4878.0, "theta_per_day_usd": -41.0, "delta_dollars": 29462.0}]},
        ]
        summary = portfolio_summary({"cash_usd": 13109.25}, results)
        self.assertEqual(summary["equity"], round(13109.25 + 17369 + 5100, 2))
        self.assertEqual(summary["unrealized_pnl"], round(17369 + 5100 - 17150 - 4878, 2))
        self.assertEqual(summary["exposure"][0]["symbol"], "AMD")

    def test_csp_sizing_and_management_lines(self) -> None:
        put = Contract("XP90", "put", 90.0, 0, 30, 1.9, 2.1, 2.0, 10, 500, 0.4)
        row = csp_candidate(put, 100.0, 0.04, 20000, None)
        self.assertEqual(row["contracts_within_limit"], 2)
        self.assertEqual(row["cash_reserved"], 18000)
        self.assertEqual(row["breakeven"], 88.0)
        self.assertEqual(row["take_profit_buyback"], {"60pct": 0.8, "80pct": 0.4})
        lines = row["management_lines"]
        self.assertGreater(lines["delta_0.35"]["spot"], lines["delta_0.40"]["spot"])
        self.assertGreater(lines["delta_0.40"]["spot"], lines["delta_0.50"]["spot"])
        self.assertEqual(lines["delta_0.35"]["action"], "roll-out-and-down")
        self.assertAlmostEqual(bs_delta("put", lines["delta_0.40"]["spot"], 90, 30 / 365, 0.04, 0.4), -0.40, places=2)

    def test_delta_range_rejects_out_of_band_puts(self) -> None:
        put = Contract("XP97", "put", 97.0, 0, 30, 2.9, 3.1, 3.0, 10, 500, 0.4)
        self.assertIsNotNone(pick_by_delta([put], 100, 0.04, 0.3, min_open_interest=100, max_spread_pct=0.25, max_delta_gap=0.2))
        self.assertIsNone(pick_by_delta([put], 100, 0.04, 0.3, min_open_interest=100, max_spread_pct=0.25, max_delta_gap=0.2, delta_range=(0.18, 0.35)))


if __name__ == "__main__":
    unittest.main()
