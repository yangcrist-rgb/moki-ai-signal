from __future__ import annotations

import argparse
import json
from pathlib import Path

from moki_signal.pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a fixed-template MOKI AI Signal chart for one US ticker")
    parser.add_argument("ticker", help="US ticker, for example AEHR")
    parser.add_argument("--days", type=int, default=93, help="Calendar days displayed")
    parser.add_argument("--as-of", help="Latest date included, YYYY-MM-DD; defaults to the latest completed US session")
    parser.add_argument("--strategy", default="s1-reconstruction")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd() / "outputs")
    parser.add_argument("--cache-dir", type=Path, default=Path.cwd() / ".moki-cache")
    parser.add_argument("--refresh", action="store_true", help="Fail rather than fall back to cache if the download fails")
    parser.add_argument("--skip-data-validation", action="store_true", help="Skip the independent Sina daily OHLC cross-check")
    args = parser.parse_args()
    result = run_pipeline(args.ticker, output_dir=args.output_dir, cache_dir=args.cache_dir, days=args.days, as_of=args.as_of, strategy_id=args.strategy, refresh=args.refresh, validate_data=not args.skip_data_validation)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
