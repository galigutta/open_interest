# open_interest

Builds an open-interest hedge table (call/put delta-hedge shares by expiry under price shocks) from OCC series data, Black–Scholes greeks (mibian), spot from yfinance, and **30-day IV mean from AlphaQuery** (flat per-symbol fallback if the scrape fails).

Works for **OCC equity underlyings** (`symbolType=U`). Default watchlist is **single-stock only** (no SPY/QQQ/IWM).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```text
python oi.py [SYMBOL ...] [--price P] [--vol V] [--step S]
python oi.py --watchlist
python oi.py --watchlist-file watchlist.txt
python oi.py --config config.yaml
```

| Arg / flag | Default | Notes |
|------------|---------|--------|
| `SYMBOL …` | `TSLA` | One or more OCC / Yahoo tickers |
| `--watchlist` / `-w` | | Run `watchlist.txt`, else `config.yaml`, else built-in list |
| `--watchlist-file PATH` | | Explicit ticker list file |
| `--config PATH` | | YAML with `symbols:` / `watchlist:` |
| `--price` / `--vol` / `--step` | live feeds / `100` | Overrides (also legacy: `python oi.py 350 55 100` → TSLA) |

### Examples

```bash
python oi.py                          # TSLA
python oi.py SPCX
python oi.py TSLA NVDA AAPL
python oi.py --watchlist              # all single-stock names in watchlist.txt
python oi.py SPCX 151 52 50           # symbol + price + IV% + step
```

### Default watchlist (single-stock)

`TSLA NVDA AAPL AMZN META GOOGL MSFT AMD NFLX SPCX`

Edit `watchlist.txt` or `config.yaml` to add names (OCC equity series required).

### Local outputs (`snapshot/`)

- `snapshot/{SYMBOL}-{YYYY-MM-DD}` — cleaned OCC OI CSV
- `snapshot/{SYMBOL}-{YYYY-MM-DD}-summary.csv` — hedge by expiry × shock
- `snapshot/{SYMBOL}-index.html` — HTML hedge table
- `snapshot/{SYMBOL}-index.csv` / `*-so.csv` — rolling daily summary rows

For `TSLA` only, root `index.html` / `so.csv` / `index.csv` are also written (legacy S3 layout).

## Data sources & history

1. **OCC open interest** — `https://marketdata.theocc.com/series-search?symbolType=U&symbol={SYMBOL}` (browser User-Agent). Point-in-time only; **daily runs** are required to accumulate ~30-day hedge history in `*-index.csv`.
2. **Spot** — yfinance.
3. **IV** — **AlphaQuery** `https://www.alphaquery.com/stock/{SYMBOL}/volatility-option-statistics/30-day/iv-mean` (30-day IV mean scrape). If HTTP/parse fails, uses a **flat** per-symbol default from `DEFAULT_VOLS` (else ~52) and logs the failure.

ETFs (SPY/QQQ/IWM/etc.) are intentionally **out of scope** for the default watchlist (single-stock only).


## S3 (optional)

Skipped when AWS credentials are missing. `OI_S3_BUCKET` or `OI_S3_BUCKET_TEMPLATE` override per-symbol defaults (`tsla-oi`, `spcx-oi`, else `{symbol}-oi`).

## Docker

`Dockerfile` `CMD` defaults to `python oi.py` (TSLA). For the watchlist: `python oi.py --watchlist`.
