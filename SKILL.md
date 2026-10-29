---
name: moki-ai
description: Generate a fixed-template MOKI AI Signal chart for a US stock ticker with a self-contained Yahoo daily-data loader, independent Sina OHLC validation, and the authorized RSI/ZigZag reconstruction. Use when a user asks MOKI AI to run a ticker, create a B/S signal chart, export signal or OHLCV files, verify daily prices, or reproduce the standard MOKI AI Signal image. Also use for 「扫描」 / US market scans: index and Magnificent Seven trends, oil and yields, large-cap earnings, sector rank and leaders, option-volume anomalies, GEX, and stock/options playbooks.
---

# MOKI AI

Generate one MOKI AI Signal chart for one US equity ticker. Use only the files bundled with this Skill; do not require a separately installed `global-stock-data` Skill. The chart is a deterministic visual result from the local engine; do not redraw it manually or change its template.

## Workflow

1. Accept one ticker. Treat `days` and `as-of` as optional; default to 93 days and the latest completed US trading session.
2. From the installed Skill directory, run `scripts/bootstrap_signal.py` with an explicit absolute output directory in the current task workspace. On first use it creates `.moki-runtime` and installs the pinned dependencies there.
3. Inspect the returned JSON and PNG. Report the latest confirmed signal, pending reversal level, data end date, `data.validation.status`, and links to PNG, CSV, and manifest. Treat `warning` as a review flag and `unavailable` as a provider-access limitation; neither means the strategy used Sina data.
4. State that B/S markers are drawn on pivot bars but become known only on their later confirmation bars. Do not present them as investment advice.

## Command

Run from the Skill root:

```powershell
python scripts/bootstrap_signal.py AEHR --output-dir .\outputs
```

Use `--as-of YYYY-MM-DD` for reproducible historical results, `--days N` to change the visible calendar window, and `--refresh` to bypass an available local cache. Use `--skip-data-validation` only for diagnostics when Sina cannot be reached.

## US market indicator scan

When the user says 「扫描」 or asks for index/sector breadth, recent leaders, or stock/options strategies, run from the Skill root:

```powershell
python scripts/us_market_indicator.py --universe configs/us_market_universe.json --output-dir C:\absolute\workspace\outputs\market-indicator
```

Read `market-scan.md` and present it as compact Markdown tables in this order: indices and Magnificent Seven trend with SMA 5/10/20/30/50/100; oil and 10Y/30Y yields; today's ≥$300B earnings (including the previous session's after-close reports) with impact on linked tickers; next-30-day events; sector 20-day rank, breadth, and top names by 5-day return; option-volume anomalies and estimated GEX; stock plans and aggressive/conservative option playbooks for leaders of the top 5 sectors.

- Option-volume anomalies need three prior daily snapshots and a 2x median threshold; report `building-baseline` until then.
- GEX is a Yahoo-chain/Black-Scholes estimate, not dealer inventory. Investor days come only from `events.manual` or explicit web research.
- Playbooks: bullish → buy-call / cash-secured-put (+ covered-call if long ≥100 shares); bearish → buy-put / cheaper OTM put, never naked calls; range → no directional trade, covered-call or CSP.
- Pass `--profile configs/options_agent_profile.json` so symbols with ≥100 shares also get covered-call legs.
- Flag contracts marked thin and any earnings before expiry. Use `--skip-options` only when the user wants the index/sector tape alone. This scan is not investment advice and places no orders.

## Options decision agent

For holdings / watchlist option decisions, follow [references/mcmillan-options-agent-playbook.md](references/mcmillan-options-agent-playbook.md) and run:

```powershell
python scripts/options_decision_agent.py --profile configs/options_agent_profile.json --output-dir C:\absolute\workspace\outputs\options-agent
```

If the profile is missing, copy `configs/options_agent_profile.example.json` and ask the user to fill in account size, symbols, and shares. Report trend (SMA 20/50/200), GEX walls as context only, cash-secured-put candidates with contracts and cash reserved within the per-trade limit, 60%/80% take-profit buybacks, and the Δ0.35 roll / Δ0.40 defense / Δ0.50 assignment-review stock prices. Never suggest short puts in a clear bearish trend or naked short calls.

## Boundaries

- Support US daily OHLC only in version 1.
- Keep Yahoo as the only strategy-input provider. Use Sina only to validate recent OHLC cells; never merge or substitute its rows. Read [references/data-contract.md](references/data-contract.md) before changing data behavior.
- Use `s1-reconstruction` by default. See [references/strategy-contract.md](references/strategy-contract.md) before adding another strategy.
- Preserve `moki-ai-signal-v1`; create a new template version for any visual change.
