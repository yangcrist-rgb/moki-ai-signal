from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from moki_signal.data import default_as_of
from moki_signal.market_scan import run_scan
from moki_signal.scan_report import render_markdown


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="US market indicator scan: indices, macro, events, sectors, options playbooks")
    parser.add_argument("--universe", type=Path, default=ROOT / "configs" / "us_market_universe.json")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd() / "outputs" / "market-indicator")
    parser.add_argument("--cache-dir", type=Path, default=Path.cwd() / ".moki-cache")
    parser.add_argument("--as-of", help="Latest daily bar included, YYYY-MM-DD; option chains are always a live snapshot")
    parser.add_argument("--skip-options", action="store_true", help="Index/sector tape only; no option chains")
    parser.add_argument("--skip-events", action="store_true", help="Skip earnings and manual events")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    config = json.loads(args.universe.read_text(encoding="utf-8"))
    as_of = pd.Timestamp(args.as_of) if args.as_of else default_as_of()
    result = run_scan(config, as_of=as_of, cache_dir=args.cache_dir, skip_options=args.skip_options, skip_events=args.skip_events, workers=args.workers)
    target = args.output_dir / (result["data_end"] or as_of.strftime("%Y-%m-%d"))
    target.mkdir(parents=True, exist_ok=True)
    json_path, md_path = target / "market-scan.json", target / "market-scan.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps({"data_end": result["data_end"], "regime": result["regime"], "json": str(json_path.resolve()), "markdown": str(md_path.resolve()), "warnings": len(result["warnings"])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
