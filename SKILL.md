---
name: moki-ai
description: Generate a fixed-template MOKI AI Signal chart for a US stock ticker using the authorized public-source RSI and ZigZag reconstruction. Use when a user asks MOKI AI to run a ticker, create a B/S signal chart, export the signal CSV, or reproduce the standard MOKI AI Signal image.
---

# MOKI AI

Generate one MOKI AI Signal chart for one US equity ticker. The chart is a deterministic visual result from the local engine; do not redraw it manually or change its template.

## Workflow

1. Accept one ticker. Treat `days` and `as-of` as optional; default to 93 days and the latest completed US trading session.
2. Run `scripts/run_signal.py` with an explicit output directory in the current task workspace.
3. Inspect the returned JSON and the PNG. Report the latest confirmed signal, pending reversal level, data end date, and links to PNG, CSV, and manifest.
4. State that B/S markers are drawn on pivot bars but become known only on their later confirmation bars. Do not present them as investment advice.

## Command

```powershell
python scripts/run_signal.py AEHR --output-dir .\outputs\AEHR
```

Use `--as-of YYYY-MM-DD` for reproducible historical results, `--days N` to change the visible calendar window, and `--refresh` to bypass an available local cache.

## Boundaries

- Support US daily OHLC only in version 1.
- Use `s1-reconstruction` by default. See [references/strategy-contract.md](references/strategy-contract.md) before adding another strategy.
- Preserve `moki-ai-signal-v1`; create a new template version for any visual change.
