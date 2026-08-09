from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from moki_signal.data import _parse_sina_daily, compare_daily_frames  # noqa: E402


def sample_frame(close_delta: float = 0.0) -> pd.DataFrame:
    rows = []
    for day in range(1, 7):
        base = 10.0 + day
        rows.append(
            {
                "Date": pd.Timestamp(f"2026-08-0{day}"),
                "Open": base,
                "High": base + 1.0,
                "Low": base - 1.0,
                "Close": base + 0.5 + (close_delta if day == 6 else 0.0),
                "Volume": 1000 + day,
            }
        )
    return pd.DataFrame(rows)


class SinaParserTests(unittest.TestCase):
    def test_parses_jsonp_into_normalized_frame(self) -> None:
        payload = 'var x=([{"d":"2026-08-07","o":"10.1","h":"10.8","l":"9.9","c":"10.5","v":"1234"}])'
        frame = _parse_sina_daily(payload)

        self.assertEqual(list(frame.columns), ["Date", "Open", "High", "Low", "Close", "Volume"])
        self.assertEqual(frame.iloc[0]["Date"], pd.Timestamp("2026-08-07"))
        self.assertEqual(float(frame.iloc[0]["Close"]), 10.5)
        self.assertEqual(int(frame.iloc[0]["Volume"]), 1234)

    def test_rejects_non_jsonp_response(self) -> None:
        with self.assertRaisesRegex(ValueError, "unrecognized"):
            _parse_sina_daily("blocked")


class DailyComparisonTests(unittest.TestCase):
    def test_matching_frames_pass(self) -> None:
        primary = sample_frame()
        result = compare_daily_frames(primary, primary.copy(), lookback=5)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["checked_bars"], 5)
        self.assertEqual(result["matched_price_cells"], 20)
        self.assertEqual(result["mismatches"], [])

    def test_future_reference_rows_do_not_break_historical_as_of(self) -> None:
        primary = sample_frame()
        future = pd.DataFrame(
            [
                {
                    "Date": pd.Timestamp("2026-08-07"),
                    "Open": 20.0,
                    "High": 21.0,
                    "Low": 19.0,
                    "Close": 20.5,
                    "Volume": 2000,
                }
            ]
        )
        reference = pd.concat([primary.copy(), future], ignore_index=True)
        result = compare_daily_frames(primary, reference, lookback=5)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["reference_last_date"], "2026-08-06")

    def test_price_difference_warns_without_mutating_primary(self) -> None:
        primary = sample_frame()
        reference = sample_frame(close_delta=1.0)
        original_close = float(primary.iloc[-1]["Close"])

        result = compare_daily_frames(primary, reference, lookback=5)

        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["reason"], "date-or-price-mismatch")
        self.assertEqual(result["mismatches"][0]["field"], "close")
        self.assertEqual(float(primary.iloc[-1]["Close"]), original_close)


if __name__ == "__main__":
    unittest.main()
