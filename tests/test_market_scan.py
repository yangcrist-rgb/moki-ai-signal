from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from moki_signal.market_scan import (  # noqa: E402
    build_macro_events,
    classify_trend,
    earnings_session,
    gex_change,
    is_strong,
    macro_snapshot,
    rank_sectors,
    stock_plan,
    trend_snapshot,
)
from moki_signal.universe import normalize_universe  # noqa: E402
from moki_signal.options_math import (  # noqa: E402
    Contract,
    bs_delta,
    bs_price,
    implied_vol,
    gex_summary,
    pick_by_delta,
    pick_expiration,
    spot_for_put_delta,
    volume_anomaly,
)


def price_frame(closes: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=len(closes))
    return pd.DataFrame({"Date": dates, "Open": closes, "High": [c * 1.01 for c in closes], "Low": [c * 0.99 for c in closes], "Close": closes, "Volume": 1000})


def contract(kind: str, strike: float, *, oi: int = 500, bid: float = 1.0, ask: float = 1.1, iv: float = 0.4, dte: int = 30) -> Contract:
    return Contract(f"X{kind[0].upper()}{strike}", kind, strike, 0, dte, bid, ask, 1.05, 10, oi, iv)


class TrendTests(unittest.TestCase):
    def test_rising_series_is_bullish(self) -> None:
        snapshot = trend_snapshot(price_frame([100 + i for i in range(120)]), [5, 10, 20, 30, 50, 100])
        self.assertEqual(snapshot["trend"], "bullish")
        self.assertTrue(all(snapshot["above_sma"].values()))
        self.assertAlmostEqual(snapshot["ret_1d"], round((219 / 218 - 1) * 100, 2))

    def test_falling_series_is_bearish(self) -> None:
        snapshot = trend_snapshot(price_frame([300 - i for i in range(120)]), [20, 50])
        self.assertEqual(snapshot["trend"], "bearish")

    def test_mixed_alignment_is_range(self) -> None:
        self.assertEqual(classify_trend(105, {20: 100, 50: 110}, 1.0), "range")
        self.assertEqual(classify_trend(105, {20: None, 50: 110}, 1.0), "insufficient-data")

    def test_yield_changes_reported_in_basis_points(self) -> None:
        snapshot = macro_snapshot(price_frame([4.0] * 21 + [4.25]), "yield")
        self.assertEqual(snapshot["chg_1d"], 25.0)
        self.assertEqual(snapshot["change_unit"], "bp")


class SectorTests(unittest.TestCase):
    def test_etf_score_preferred_and_leaders_by_5d(self) -> None:
        snaps = {
            "ETF": {"ret_20d": 3.0, "ret_5d": 1.0, "above_sma": {"20": True, "50": True}, "trend": "bullish"},
            "A": {"ret_20d": 10.0, "ret_5d": 1.0, "close": 110, "sma": {"20": 100, "50": 95}, "days_above_sma5": 3, "above_sma": {"20": True, "50": False}, "trend": "range"},
            "B": {"ret_20d": -2.0, "ret_5d": 6.0, "close": 90, "sma": {"20": 100, "50": 105}, "days_above_sma5": 0, "above_sma": {"20": False, "50": False}, "trend": "range"},
            "C": {"ret_20d": 1.0, "ret_5d": 2.0, "close": 50, "sma": {"20": 48, "50": 45}, "days_above_sma5": 2, "above_sma": {"20": True, "50": True}, "trend": "bullish"},
        }
        sectors = {"with_etf": {"etf": "ETF", "members": ["A", "B"]}, "no_etf": {"etf": None, "members": ["A", "C"]}}
        ranked = rank_sectors(sectors, snaps, 1)
        self.assertEqual([item["key"] for item in ranked], ["no_etf", "with_etf"])
        self.assertEqual(ranked[0]["score_20d"], 5.5)
        self.assertEqual(ranked[1]["score_basis"], "ETF 20d")
        self.assertEqual(ranked[1]["members"][0]["ticker"], "B")
        self.assertEqual(ranked[1]["leaders"], ["A"])
        self.assertEqual(ranked[0]["strong"], ["C", "A"])
        self.assertEqual(ranked[1]["breadth_above_sma20"], 50)


class OptionsMathTests(unittest.TestCase):
    def test_put_delta_round_trip(self) -> None:
        spot = spot_for_put_delta(100, 30 / 365, 0.04, 0.4, 0.35)
        self.assertAlmostEqual(bs_delta("put", spot, 100, 30 / 365, 0.04, 0.4), -0.35, places=6)
        self.assertGreater(spot, 100)

    def test_implied_vol_inverts_price(self) -> None:
        price = bs_price("call", 100, 110, 0.5, 0.04, 0.45)
        self.assertAlmostEqual(implied_vol("call", price, 100, 110, 0.5, 0.04), 0.45, places=4)
        self.assertIsNone(implied_vol("call", 0.0, 100, 110, 0.5, 0.04))

    def test_anomaly_requires_baseline(self) -> None:
        self.assertEqual(volume_anomaly(1000, [100, 100])["status"], "building-baseline")
        self.assertEqual(volume_anomaly(250, [100, 120, 90])["status"], "spike")
        self.assertEqual(volume_anomaly(150, [100, 120, 90])["status"], "normal")

    def test_gex_walls_and_regime(self) -> None:
        chain = [contract("call", 110, oi=5000), contract("call", 105, oi=100), contract("put", 90, oi=4000), contract("put", 95, oi=100)]
        summary = gex_summary(chain, 100, 0.04)
        self.assertEqual(summary["call_wall"], 110)
        self.assertEqual(summary["put_wall"], 90)

    def test_pick_by_delta_respects_liquidity_and_gap(self) -> None:
        chain = [contract("put", 95, oi=10), contract("put", 94), contract("put", 70)]
        picked = pick_by_delta(chain, 100, 0.04, 0.3, min_open_interest=100, max_spread_pct=0.25)
        self.assertIsNotNone(picked)
        self.assertEqual(picked[0].strike, 94)
        self.assertIsNone(pick_by_delta([contract("put", 70)], 100, 0.04, 0.3, min_open_interest=100, max_spread_pct=0.25))

    def test_pick_expiration_prefers_liquidity(self) -> None:
        now = 0
        weekly, monthly = 30 * 86400, 25 * 86400
        self.assertEqual(pick_expiration([weekly, monthly], now, min_dte=21, max_dte=45, target_dte=30), weekly)
        self.assertEqual(pick_expiration([weekly, monthly], now, min_dte=21, max_dte=45, target_dte=30, open_interest={weekly: 10, monthly: 900}), monthly)


class PlanTests(unittest.TestCase):
    def test_bullish_plan_levels(self) -> None:
        snapshot = {"trend": "bullish", "sma": {"5": 110.0, "10": 108.0, "20": 104.0, "50": 98.0}, "high_20d": 112.0, "low_20d": 95.0}
        plan = stock_plan(snapshot, {"call_wall": 115.0, "put_wall": 100.0})
        self.assertEqual(plan["entry_zone"], [104.0, 110.0])
        self.assertEqual(plan["invalidate_below"], 98.0)
        self.assertEqual(plan["target"], 115.0)

    def test_earnings_session_labels(self) -> None:
        self.assertEqual(earnings_session(int(pd.Timestamp("2026-10-28 20:05", tz="UTC").timestamp())), "AMC")
        self.assertEqual(earnings_session(int(pd.Timestamp("2026-10-29 11:00", tz="UTC").timestamp())), "BMO")


if __name__ == "__main__":
    unittest.main()


class StrengthAndEventTests(unittest.TestCase):
    def test_days_above_sma5_and_strength(self) -> None:
        rising = trend_snapshot(price_frame([100 + i for i in range(120)]), [5, 20, 50], (5, 20))
        self.assertGreaterEqual(rising["days_above_sma5"], 2)
        self.assertTrue(is_strong(rising))
        dipped = trend_snapshot(price_frame([100 + i for i in range(119)] + [200]), [5, 20, 50], (5, 20))
        self.assertEqual(dipped["days_above_sma5"], 0)
        self.assertFalse(is_strong(dipped))

    def test_gex_change_flags(self) -> None:
        current = {"status": "estimated", "regime": "short-gamma", "call_wall": 110, "put_wall": 95, "net_gex_usd_per_1pct": -2e6}
        self.assertEqual(gex_change(None, current, 100)["status"], "no-prior-snapshot")
        previous = {"regime": "long-gamma", "call_wall": 105, "put_wall": 95, "net_gex_usd_per_1pct": 1e6}
        change = gex_change(previous, current, 100)
        self.assertEqual(change["status"], "changed")
        self.assertEqual(len(change["flags"]), 3)
        self.assertEqual(gex_change(current, current, 100)["status"], "stable")

    def test_macro_events_split_recent_and_upcoming(self) -> None:
        config = {"events": {"macro_lookback_days": 3, "lookahead_days": 30, "macro": [
            {"date": "2026-10-28", "type": "FOMC"}, {"date": "2026-11-06", "type": "NFP"}, {"date": "2026-09-01", "type": "CPI"}]}}
        events = build_macro_events(config, pd.Timestamp("2026-10-29"))
        self.assertEqual([row["type"] for row in events["recent"]], ["FOMC"])
        self.assertEqual([row["type"] for row in events["upcoming"]], ["NFP"])
        self.assertEqual(events["upcoming"][0]["days_from_as_of"], 8)

    def test_universe_maps_lenses_macro_and_strength(self) -> None:
        raw = {"indices": [{"symbol": "SPY"}], "market_lenses": [{"symbol": "SOXX", "label": "半导体"}],
               "events": {"macro": [{"date": "2026-11-06"}]}, "strength": {"min_days_above_sma5": 3}}
        config = normalize_universe(raw)
        self.assertEqual(list(config["indices"]), ["SPY", "SOXX"])
        self.assertEqual(len(config["events"]["macro"]), 1)
        self.assertEqual(config["strength"]["min_days_above_sma5"], 3)
