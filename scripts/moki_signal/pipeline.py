from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from .chart import render_chart
from .contracts import to_jsonable
from .data import default_as_of, load_yahoo_daily, validate_yahoo_daily
from .render import TEMPLATE_ID, write_signals
from .strategy import STRATEGY_ID, run_strategy


DEFAULT_PARAMETERS: dict[str, float | int] = {"rsi_period": 14, "threshold": 0.08, "pivot_window": 2}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_pipeline(ticker: str, *, output_dir: Path, cache_dir: Path, days: int = 93, as_of: str | None = None, strategy_id: str = STRATEGY_ID, refresh: bool = False, validate_data: bool = True) -> dict[str, object]:
    if days < 15:
        raise ValueError("days must be at least 15")
    target_as_of = pd.Timestamp(as_of) if as_of else default_as_of()
    frame, instrument, stale_cache = load_yahoo_daily(ticker, as_of=target_as_of, cache_dir=cache_dir, refresh=refresh)
    data_validation = validate_yahoo_daily(frame, instrument.ticker) if validate_data else {
        "status": "skipped",
        "provider": "sina-finance-us-daily",
        "role": "validation-only",
        "affects_strategy_input": False,
    }
    result = run_strategy(strategy_id, frame, DEFAULT_PARAMETERS.copy())
    final_as_of = frame["Date"].iloc[-1].strftime("%Y-%m-%d")
    target = output_dir / instrument.ticker / final_as_of
    target.mkdir(parents=True, exist_ok=True)
    chart_path, signals_path = target / "moki-ai-signal.png", target / "signals.csv"
    data_path, manifest_path = target / "ohlcv.csv", target / "manifest.json"
    write_signals(frame, result, signals_path)
    render_summary = render_chart(frame, instrument, result, output=chart_path, days=days, stale_cache=stale_cache)
    frame.to_csv(data_path, index=False)
    summary = {
        "ticker": instrument.ticker,
        "company_name": instrument.company_name,
        "currency": instrument.currency,
        "exchange": instrument.exchange,
        "bars": int(len(frame)),
        "first_date": frame["Date"].iloc[0].strftime("%Y-%m-%d"),
        "last_date": final_as_of,
        "stale_cache": stale_cache,
        "data": {
            "primary_provider": "yahoo-finance-chart",
            "validation": data_validation,
        },
        "template": {"id": TEMPLATE_ID},
        "strategy": to_jsonable(result),
        "render": render_summary,
        "files": {"image": str(chart_path.resolve()), "signals_csv": str(signals_path.resolve()), "ohlcv_csv": str(data_path.resolve()), "manifest": str(manifest_path.resolve())},
    }
    summary["files"]["image_sha256"] = _sha256(chart_path)
    summary["files"]["signals_sha256"] = _sha256(signals_path)
    manifest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
