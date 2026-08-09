# Data contract

## Runtime independence

MOKI AI Signal must run from this repository alone. It must not import, invoke,
or require a separately installed `global-stock-data` Skill. Python 3.10 or
newer and network access to the selected market-data providers are still
required.

## Provider roles

- Yahoo Finance chart is the primary daily OHLCV input for
  `s1-reconstruction@1.0.0`.
- Sina Finance US daily K-line is a validation-only source. It must not replace,
  merge into, or mutate the Yahoo frame used by the strategy.
- A Sina failure records `unavailable` in `manifest.json` and does not prevent
  chart generation.
- `--skip-data-validation` is an explicit diagnostic escape hatch. Do not use it
  by default.

The validator compares the latest ten overlapping trading dates. It checks
open, high, low, and close with a tolerance of the greater of USD 0.02 or 0.1%
of the compared price. Volume is intentionally excluded because vendor volume
normalization can differ. A warning is evidence for review, not proof that
either provider is wrong.

## Provenance and licensing

The Sina request and JSONP parsing approach in
`scripts/moki_signal/data.py` is adapted and modified from Simon Lin's
[`global-stock-data`](https://github.com/simonlin1212/global-stock-data)
`us_stock_kline_sina` implementation. The upstream project is Apache-2.0
licensed. The MOKI project owner confirms complete written authorization for
the relevant upstream code and the configured Yahoo/Sina access used by this
product. Retain the source notice and
`licenses/global-stock-data-APACHE-2.0.txt` when redistributing this project.

The authorization documents are not distributed in this repository. The
repository distributes data-access code, not market data. Downstream users must
stay within the scope of the applicable grants, provider terms, and law; this
repository does not independently enlarge or sublicense those rights.
