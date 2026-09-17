# open_interest

Builds **open-interest hedge tables** (call/put delta-hedge shares by expiry under price shocks) from OCC series data, Black–Scholes greeks (mibian), **spot from yfinance**, and **30-day IV mean from AlphaQuery** (flat ~**52%** fallback if the scrape fails).

Works for **any OCC equity underlying** (`symbolType=U`). Default watchlist is **single-stock only** — no SPY/QQQ/IWM.

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
| `--price` | yfinance | Spot override |
| `--vol` | AlphaQuery 30-day IV mean | IV in **percent**; flat ~52% if scrape fails |
| `--step` | `100` | Shock magnitude (`±step, ±step/2, ±step/5, 0`) |

Legacy: `python oi.py 350 55 100` implies TSLA with price/vol/step.

### One ticker vs watchlist

```bash
python oi.py SPCX                     # one symbol
python oi.py NVDA --step 50
python oi.py NVDA TSLA AAPL SPCX      # explicit batch
python oi.py --watchlist              # all names in watchlist.txt
```

Batch mode isolates failures: one bad symbol logs `FAILED` and the rest continue. Exit code is non-zero only if **every** symbol failed.

### Default watchlist (single-stock)

`NVDA TSLA AAPL MSFT AMZN META AMD MU INTC NFLX SPCX`

### How to add a ticker

1. Confirm OCC has series:  
   `https://marketdata.theocc.com/series-search?symbolType=U&symbol=YOURTICKER`
2. Add the ticker to `watchlist.txt` (or `config.yaml` / `tickers.json`).
3. Run `python oi.py YOURTICKER` (or `--watchlist`).
4. Optional S3: `OI_S3_BUCKET_TEMPLATE="{symbol}-oi"` (skipped without AWS creds).
5. Rebuild the static dashboard: `python build_site.py --out ../spcx-oi-site`

### Local outputs (`snapshot/`)

| File | Meaning |
|------|---------|
| `{SYMBOL}-{YYYY-MM-DD}` | Cleaned OCC OI CSV |
| `{SYMBOL}-{YYYY-MM-DD}-summary.csv` | Hedge by expiry × shock |
| `{SYMBOL}-index.html` | HTML hedge table |
| `{SYMBOL}-index.csv` / `*-so.csv` | Rolling summary history |

For `TSLA` only, root `index.html` / `so.csv` / `index.csv` are also written (legacy S3 layout).

## Data sources

1. **OCC open interest** — `series-search?symbolType=U&symbol={SYMBOL}` (browser User-Agent). Point-in-time; daily runs accumulate history in `index.csv`.
2. **Spot** — **yfinance** (`fast_info` / recent history / `info`).
3. **IV** — scrape **AlphaQuery 30-day IV mean** (`.../stock/{SYMBOL}/volatility-option-statistics/30-day/iv-mean`). If HTTP/parse fails, fall back to flat ~**52%** (or a per-symbol entry in `DEFAULT_VOLS`) and log the fallback.

## S3 (optional)

Skipped when AWS credentials are missing. Per-symbol bucket defaults to `{symbol}-oi` (aliases: `tsla-oi`, `spcx-oi`). Override with `OI_S3_BUCKET` or `OI_S3_BUCKET_TEMPLATE`.

## Static site

```bash
python build_site.py --out /path/to/spcx-oi-site
```

Generates a multi-ticker **Options OI hedge tables** dashboard (symbol index + per-symbol pages). See [galigutta/spcx-oi-site](https://github.com/galigutta/spcx-oi-site).

## Docker

`Dockerfile` `CMD` defaults to `python oi.py` (TSLA). For the watchlist: `python oi.py --watchlist`.
