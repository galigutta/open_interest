# open_interest

OCC open-interest hedge tables (call/put delta-hedge shares by expiry under spot shocks).

- **Spot:** yfinance
- **IV:** [AlphaQuery](https://www.alphaquery.com) 30-day IV mean scrape (`/stock/{SYMBOL}/volatility-option-statistics/30-day/iv-mean`); **flat** per-symbol fallback (`DEFAULT_VOLS`, else 52) if scrape fails
- **OI:** OCC `https://marketdata.theocc.com/series-search?…` (point-in-time; run **daily** to build history)

Default watchlist is **single-stock only** (no SPY/QQQ/IWM):
`TSLA NVDA AAPL AMZN META GOOGL MSFT AMD NFLX SPCX`

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```text
python oi.py [SYMBOL ...] [--price P] [--vol V] [--step S]
python oi.py --watchlist
python oi.py --watchlist-file watchlist.txt
python oi.py --config config.yaml
```

Examples:

```bash
python oi.py                 # TSLA
python oi.py SPCX
python oi.py NVDA TSLA AAPL
python oi.py --watchlist
python oi.py 350 55 100      # legacy → TSLA
```

Outputs under `snapshot/`: `{SYMBOL}-{date}`, `*-summary.csv`, `*-index.html`, `*-index.csv`.

Optional S3 via `OI_S3_BUCKET` / template; skipped without AWS credentials.

## Site

`build_site.py` and/or the companion Pages repo (`spcx-oi-site`, optional rename to `oi-site`) expect `data/{SYMBOL}/index.csv` + `tickers.json` for a ticker picker + history table.
