# McMillan-Style Options Agent Playbook

This note distills practical decision rules inspired by Lawrence G. McMillan's
framework in *Options as a Strategic Investment*, adapted to this repository's
agent workflow.

## Design Intent

- Use options as a risk-defined extension of stock views.
- Prefer liquid contracts and repeatable adjustment rules.
- Separate entry logic, management logic, and exit logic.

## Core Sell Put Framework (Cash-Secured)

1. Direction filter: avoid naked short puts in clear bearish trend.
2. Contract filter:
   - DTE in a manageable window (typically 25-45 days).
   - OTM strikes with moderate delta (commonly 0.18-0.35 abs delta).
   - Sufficient liquidity (OI and spread quality).
3. Capital discipline:
   - Reserve full assignment cash (`strike * 100 * contracts`).
   - Cap single-position risk as a fraction of account equity.
4. Management:
   - Profit-taking zone near 60-80% of max credit.
   - Roll trigger near abs delta ~0.35.
   - Mandatory defense near abs delta ~0.40.
   - Assignment readiness / hard-risk review near abs delta ~0.50.
5. Roll style:
   - "Out and down" when possible: later expiry and lower strike.
   - Prefer net credit roll; avoid repeated debit rolls.

## GEX / Wall Context Layer

The agent also computes approximate gamma exposure by strike:

- Positive wall clusters can behave like magnetic / damping zones.
- Negative wall clusters can increase local instability if breached.
- Use walls as context, not as standalone entry signals.

## Scan Playbooks (Aggressive vs Conservative)

When the market indicator scan ranks recent sector leaders, map the stock trend to two option styles:

- Aggressive: defined-risk directional long (call in an uptrend, put in a downtrend), typically 21-45 DTE near ATM.
- Conservative: premium-selling only when the view is not bearish (cash-secured put or covered call). In a downtrend, conservative still means a smaller long put, not a naked short call.
- Range/transition: skip aggressive directionals.

These ideas are educational overlays on the scan, not broker orders.

## Boundaries

- Educational and research tool only.
- Yahoo options chain does not provide exchange-cleared Greeks here;
  Gamma/Delta in the tool are Black-Scholes estimates from IV snapshots.
- Real execution quality depends on slippage, fills, corporate actions,
  event gaps, and broker risk controls.
