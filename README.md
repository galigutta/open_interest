# open_interest

Builds an open-interest hedge table (call/put delta-hedge shares by expiry under price shocks) from OCC series data, Black–Scholes greeks (mibian), spot from yfinance, and 30-day IV mean from alphaquery.

Originally hardcoded for **TSLA**; the CLI now accepts any OCC underlying symbol (validated for **SPCX** / SpaceX).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```text
python oi.py [SYMBOL] [price] [vol] [step]
```

| Arg | Default | Notes |
|-----|---------|--------|
| `SYMBOL` | `TSLA` | OCC / yfinance / alphaquery ticker (e.g. `SPCX`) |
| `price` | from yfinance (else built-in default) | Spot override |
| `vol` | from alphaquery 30-day IV mean (else default) | IV in **percent** (e.g. `52` = 52%) |
| `step` | `100` | Price-shock magnitude; shocks are `±step, ±step/2, ±step/5, 0` |

### Tesla (default / backward compatible)

```bash
python oi.py
python oi.py 350 55 100          # legacy: price vol step (implies TSLA)
python oi.py TSLA 350 55 50
```

### SpaceX (SPCX)

```bash
python oi.py SPCX
python oi.py SPCX 151 52 50      # override spot, IV%, shock step
```

Local outputs land under `snapshot/`:

- `snapshot/{SYMBOL}-{YYYY-MM-DD}` — cleaned OCC OI CSV
- `snapshot/{SYMBOL}-{YYYY-MM-DD}-summary.csv` — hedge by expiry × shock
- `snapshot/{SYMBOL}-index.html` — HTML hedge table
- `snapshot/{SYMBOL}-so.csv` / `snapshot/{SYMBOL}-index.csv` — rolling summary rows

For `TSLA` only, root `index.html` / `so.csv` / `index.csv` are also written (legacy S3 static site layout).

## S3 (optional)

Uploads/downloads are skipped gracefully when AWS credentials are missing so local snapshots still work.

| Env | Default |
|-----|---------|
| `OI_S3_BUCKET` | `tsla-oi` for TSLA, `spcx-oi` for SPCX, else `{symbol}-oi` |

Objects written when credentials + bucket exist:

- `snapshot/{date}.csv`
- `summary/{date}-summary.csv`
- `index.html`, `index.csv`

Create the SPCX bucket (e.g. `spcx-oi`) in your AWS account before enabling uploads.

## Data sources

1. **OCC open interest** — `https://marketdata.theocc.com/series-search?symbolType=U&symbol={SYMBOL}` (browser User-Agent). SPCX series data is available from OCC.
2. **Spot** — yfinance (`fast_info` / recent history / `info`).
3. **IV** — alphaquery `.../stock/{SYMBOL}/volatility-option-statistics/30-day/iv-mean`. If the page cannot be parsed, a flat default is used and logged (`TSLA` 55, `SPCX` 52).

## Docker / ECR

See `Dockerfile`. Container `CMD` still defaults to `python oi.py` (TSLA). To run SPCX in the image:

```bash
python oi.py SPCX
```

Rebuild the image with `--no-cache` after changing `requirements.txt`.
