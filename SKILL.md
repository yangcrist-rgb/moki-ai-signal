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

The scan loads `configs/options_agent_profile.json` by default (`--skip-holdings` to omit). Read `market-scan.md` and always reply in Chinese, presenting results as compact Markdown tables (prose only when there is very little to say), in this 7-step order:

1. Macro: 10Y/30Y Treasury yields (bp) and WTI oil, plus any headwind.
2. Market: SPY / QQQ / DIA plus SOXX and IGV — returns, SMA 5/10/20/30/50/100 position, trend, and regime.
3. Major events: macro calendar (`events.macro`: FOMC, minutes, CPI, NFP — recent 3 days and upcoming), ≥$300B earnings for today and the previous after-close, next-30-day earnings/investor days. Verify reported results with web search when they matter.
4. Sectors (七巨头、存储、光通信、服务器、芯片、算力中心、半导体设备、软件、能源、生物科技、医疗、贵金属、金融&银行、无人机、航天、综合传统行业 — the last keeps only ≥$300B names via `minimum_market_cap_usd`): 20-day rank and breadth, and strong stocks — bullish structure (close > SMA20 > SMA50) holding above the 5-day SMA for ≥2 sessions with a positive 5-day return.
5. Options by sector: near-term volume anomalies (≥2x the median of ≥3 prior snapshots) and GEX changes vs the prior snapshot (gamma flip, wall shift ≥3% of spot, net GEX change ≥50%), with walls and regime.
6. Option strategies for the strong stocks of the top 5 sectors plus strong anomaly tickers.
7. Holdings check from the options agent, followed by concrete advice per position.

- Until three snapshots exist, report `building-baseline`. GEX is a Yahoo-chain/Black-Scholes estimate, not dealer inventory. Investor days come only from `events.manual` or explicit web research.
- Outside the regular session Yahoo zeroes OI, quotes and IV; the report flags this (`chain_reset`). Say GEX and section 6 need a rerun during ET 09:30–16:00; holdings Greeks then use IV implied from the last trade.
- Playbooks: bullish → buy-call / cash-secured-put (+ covered-call if long ≥100 shares); bearish → buy-put / cheaper OTM put, never naked calls; range → no directional trade, covered-call or CSP.
- Flag contracts marked thin and any earnings before expiry. Keep `events.macro` dates current. This scan is not investment advice and places no orders.

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
