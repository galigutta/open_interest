#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Open-interest hedge table generator (multi-ticker).

Usage:
  python oi.py SYMBOL [SYMBOL ...] [--price P] [--vol V] [--step S]
  python oi.py --watchlist
  python oi.py --watchlist-file path/to/watchlist.txt
  python oi.py --config path/to/config.yaml

SYMBOL defaults to TSLA when no symbols / watchlist / config are given.
Legacy: python oi.py price volatility step  (implies TSLA).
"""

from __future__ import annotations

import argparse
import copy
import os
import re
import sys
import traceback
import warnings
from datetime import date, datetime
from io import StringIO

import boto3
import mibian
import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import requests
import urllib.request
import yfinance as yf
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

warnings.filterwarnings("ignore")

OUT_DIR = "snapshot"
DATESTR = date.today().strftime("%Y-%m-%d")
RATE = 2.0
NYSE = mcal.get_calendar("NYSE")

DEFAULT_BUCKETS = {
    "TSLA": "tsla-oi",
    "SPCX": "spcx-oi",
}
DEFAULT_PRICES = {
    "TSLA": 350.0,
    "NVDA": 120.0,
    "AAPL": 220.0,
    "AMZN": 220.0,
    "META": 550.0,
    "GOOGL": 170.0,
    "MSFT": 420.0,
    "AMD": 150.0,
    "NFLX": 700.0,
    "SPCX": 150.0,
}
DEFAULT_VOLS = {
    "TSLA": 55.0,
    "NVDA": 45.0,
    "AAPL": 25.0,
    "AMZN": 30.0,
    "META": 35.0,
    "GOOGL": 30.0,
    "MSFT": 25.0,
    "AMD": 45.0,
    "NFLX": 40.0,
    "SPCX": 52.0,
}
DEFAULT_WATCHLIST = [
    "TSLA", "NVDA", "AAPL", "AMZN", "META", "GOOGL", "MSFT",
    "AMD", "NFLX", "SPCX",
]


def default_s3_bucket(symbol: str) -> str:
    return DEFAULT_BUCKETS.get(symbol, f"{symbol.lower()}-oi")


def usage_epilog() -> str:
    return """
examples:
  python oi.py SPCX
  python oi.py NVDA TSLA AAPL
  python oi.py --watchlist
  python oi.py --watchlist-file watchlist.txt
  python oi.py --config config.yaml
  python oi.py TSLA 350 55 50          # symbol + price + vol + step
  python oi.py 350 55 100              # legacy: implies TSLA

watchlist.txt: one ticker per line (# comments and blank lines ok)
config.yaml:   symbols: [NVDA, TSLA, ...]   or one ticker per "- TICKER" line

outputs (per symbol) under snapshot/:
  {SYMBOL}-{date}            cleaned OCC OI CSV
  {SYMBOL}-{date}-summary.csv  hedge by expiry x shock
  {SYMBOL}-index.html        HTML hedge table
  {SYMBOL}-index.csv / {SYMBOL}-so.csv  rolling summary history

S3: bucket defaults to {symbol}-oi (override with OI_S3_BUCKET or
OI_S3_BUCKET_TEMPLATE="{symbol}-oi"). Skipped when AWS creds are missing.
""".strip()


def load_watchlist_txt(path: str) -> list[str]:
    symbols = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            for tok in re.split(r"[\s,;]+", line):
                tok = tok.strip().upper()
                if tok:
                    symbols.append(tok)
    return symbols


def load_config_yaml(path: str) -> list[str]:
    """Minimal YAML reader for a symbols list (no PyYAML required)."""
    text = open(path, encoding="utf-8").read()
    symbols: list[str] = []

    # Inline list: symbols: [NVDA, TSLA] or watchlist: [...]
    m = re.search(
        r"(?:^|\n)\s*(?:symbols|watchlist)\s*:\s*\[([^\]]*)\]",
        text,
        re.IGNORECASE,
    )
    if m:
        for tok in re.split(r"[\s,]+", m.group(1)):
            tok = tok.strip().strip("\"'").upper()
            if tok:
                symbols.append(tok)
        return symbols

    # Block list under symbols: / watchlist:
    in_block = False
    for line in text.splitlines():
        if re.match(r"^\s*(?:symbols|watchlist)\s*:\s*$", line, re.IGNORECASE):
            in_block = True
            continue
        if in_block:
            if re.match(r"^\S", line) and not line.strip().startswith("-"):
                break
            m2 = re.match(r"^\s*-\s+[\"']?([A-Za-z0-9.\-^]+)[\"']?\s*$", line)
            if m2:
                symbols.append(m2.group(1).upper())
            elif line.strip() and not line.strip().startswith("#"):
                # non-list content ends the block
                if not line.strip().startswith("-"):
                    break
    return symbols


def resolve_symbols(args) -> list[str]:
    """Resolve symbol list from CLI / watchlist / config / legacy."""
    # Legacy: first positional is numeric → TSLA with overrides already parsed
    if args.legacy_price is not None:
        return ["TSLA"]

    symbols: list[str] = []

    if args.config:
        if not os.path.isfile(args.config):
            raise SystemExit(f"config file not found: {args.config}")
        symbols = load_config_yaml(args.config)
        if not symbols:
            raise SystemExit(f"no symbols found in config: {args.config}")
        return symbols

    watchlist_path = args.watchlist_file
    if args.watchlist and not watchlist_path:
        for candidate in ("watchlist.txt", "config.yaml"):
            if os.path.isfile(candidate):
                watchlist_path = candidate
                break
        if watchlist_path is None:
            print(
                "No watchlist.txt or config.yaml found; "
                f"using built-in default watchlist ({len(DEFAULT_WATCHLIST)} names)"
            )
            return list(DEFAULT_WATCHLIST)

    if watchlist_path:
        if not os.path.isfile(watchlist_path):
            raise SystemExit(f"watchlist file not found: {watchlist_path}")
        if watchlist_path.lower().endswith((".yaml", ".yml")):
            symbols = load_config_yaml(watchlist_path)
        else:
            symbols = load_watchlist_txt(watchlist_path)
        if not symbols:
            raise SystemExit(f"no symbols found in {watchlist_path}")
        return symbols

    if args.symbols:
        return [s.strip().upper() for s in args.symbols if s.strip()]

    return ["TSLA"]


def parse_args(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # Detect legacy: first arg is a float (price) → TSLA
    legacy_price = legacy_vol = legacy_step = None
    if argv and not argv[0].startswith("-"):
        try:
            legacy_price = float(argv[0])
        except ValueError:
            legacy_price = None

    parser = argparse.ArgumentParser(
        prog="oi.py",
        description=(
            "Build OCC open-interest hedge tables (spot shocks × net delta hedge) "
            "for one or many optionable underlyings."
        ),
        epilog=usage_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "symbols",
        nargs="*",
        help="One or more OCC / Yahoo tickers (e.g. NVDA TSLA AAPL SPCX)",
    )
    parser.add_argument(
        "--watchlist", "-w",
        action="store_true",
        help="Run every symbol in watchlist.txt, config.yaml, or the built-in default list",
    )
    parser.add_argument(
        "--watchlist-file",
        metavar="PATH",
        help="Path to watchlist.txt (one ticker per line)",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to config.yaml with a symbols/watchlist list",
    )
    parser.add_argument(
        "--price",
        type=float,
        default=None,
        help="Override spot price (applied to each run symbol)",
    )
    parser.add_argument(
        "--vol",
        type=float,
        default=None,
        help="Override IV in percent (e.g. 52 = 52%%)",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=None,
        help="Price-shock magnitude (default 100). Shocks: ±step, ±step/2, ±step/5, 0",
    )

    # Legacy positional price/vol/step after a numeric first arg
    if legacy_price is not None:
        # Re-parse as: price [vol] [step]
        rest = argv[1:]
        vol = step = None
        try:
            if rest:
                vol = float(rest[0])
            if len(rest) > 1:
                step = float(rest[1])
        except ValueError:
            parser.print_help()
            raise SystemExit(1)
        ns = argparse.Namespace(
            symbols=[],
            watchlist=False,
            watchlist_file=None,
            config=None,
            price=legacy_price,
            vol=vol,
            step=step,
            legacy_price=legacy_price,
        )
        return ns

    # Also support: SYMBOL price vol step (positional overrides after first ticker)
    # If exactly one symbol-looking first arg and remaining are numeric, treat as overrides.
    if (
        argv
        and not argv[0].startswith("-")
        and not any(a in ("--watchlist", "-w", "--watchlist-file", "--config") for a in argv)
    ):
        # Peek: SYMBOL [price] [vol] [step]  — only when trailing args are all numeric
        tokens = []
        i = 0
        while i < len(argv) and not argv[i].startswith("-"):
            tokens.append(argv[i])
            i += 1
        if len(tokens) >= 2:
            try:
                floats = [float(t) for t in tokens[1:]]
                # First token is symbol; rest are price/vol/step
                # Only rewrite argv if ALL trailing positionals are numeric
                new_argv = [tokens[0]]
                if len(floats) >= 1:
                    new_argv += ["--price", str(floats[0])]
                if len(floats) >= 2:
                    new_argv += ["--vol", str(floats[1])]
                if len(floats) >= 3:
                    new_argv += ["--step", str(floats[2])]
                if len(floats) > 3:
                    raise ValueError("too many numeric args")
                new_argv += argv[i:]
                argv = new_argv
            except ValueError:
                pass  # multiple symbols like NVDA TSLA — leave as-is

    ns = parser.parse_args(argv)
    ns.legacy_price = None
    return ns


def build_deltas(step):
    """Match historical shape for step=100: [-100,-50,-20,0,20,50,100]."""
    step = float(step)
    return [-step, -step / 2.0, -step / 5.0, 0.0, step / 5.0, step / 2.0, step]


def greek_string(deets, iv):
    c = mibian.BS(deets, iv)
    return [
        c.callPrice,
        c.putPrice,
        c.callDelta,
        c.putDelta,
        c.callDelta2,
        c.putDelta2,
        c.callTheta,
        c.putTheta,
        c.callRho,
        c.putRho,
        c.vega,
        c.gamma,
    ]


def download_with_headers(url, output_file):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as response, open(output_file, "wb") as out_file:
        out_file.write(response.read())


def fetch_spot(symbol, default_price, err_log: list):
    day_volume = 0
    curr_price = default_price
    try:
        ticker = yf.Ticker(symbol)
        price = None
        try:
            price = getattr(ticker.fast_info, "last_price", None)
        except Exception:
            price = None
        if price is None:
            try:
                hist = ticker.history(period="5d")
                if len(hist):
                    price = float(hist["Close"].iloc[-1])
            except Exception:
                pass
        if price is None:
            info = ticker.info or {}
            price = info.get("currentPrice") or info.get("regularMarketPrice")
        if price is not None:
            curr_price = float(price)
        now = datetime.now()
        if now > now.replace(hour=16) and NYSE.valid_days(
            start_date=DATESTR, end_date=DATESTR
        ).size == 1:
            try:
                day_volume = int(
                    getattr(ticker.fast_info, "last_volume", 0)
                    or (ticker.info or {}).get("volume")
                    or 0
                )
            except Exception:
                day_volume = 0
    except Exception as e:
        err_log.append(
            f"unable to get price from yahoo for {symbol}, defaulting price: {e}"
        )
        print(e)
    return curr_price, day_volume


def fetch_iv(symbol, default_vol, err_log: list):
    flatvol = default_vol
    url_vol = (
        f"https://www.alphaquery.com/stock/{symbol}/"
        "volatility-option-statistics/30-day/iv-mean"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
        "Referer": "https://www.alphaquery.com",
    }
    try:
        resp = requests.get(url_vol, headers=headers, timeout=15)
        resp.raise_for_status()
        text = resp.text
        try:
            df_list = pd.read_html(StringIO(text))
            inner_df = df_list[0]
            flatvol = float(
                inner_df[inner_df[0] == "Implied Volatility (Mean)"][1].iloc[0]
            ) * 100
            return flatvol
        except Exception:
            m = re.search(
                r"Implied Volatility \(Mean\)[^0-9]{0,200}?([0-9]+\.[0-9]+)",
                text,
                re.IGNORECASE | re.DOTALL,
            )
            if not m:
                m = re.search(
                    r"had 30-Day Implied Volatility \(Mean\) of\s*<strong>([0-9.]+)</strong>",
                    text,
                    re.IGNORECASE,
                )
            if m:
                flatvol = float(m.group(1)) * 100
                return flatvol
            raise
    except Exception as e:
        msg = (
            f"unable to get vol from alphaquery for {symbol}, "
            f"defaulting vol={default_vol}: {e}"
        )
        err_log.append(msg)
        print(msg)
    return flatvol


def s3_client_or_none():
    try:
        session = boto3.Session()
        creds = session.get_credentials()
        if creds is None:
            return None
        return session.client("s3")
    except Exception as e:
        print(f"S3 unavailable ({e}); continuing with local snapshots only")
        return None


def resolve_bucket(symbol: str) -> str:
    explicit = os.environ.get("OI_S3_BUCKET")
    if explicit:
        # If running a batch, a single fixed bucket may be intentional; still allow template
        template = os.environ.get("OI_S3_BUCKET_TEMPLATE")
        if template:
            return template.format(symbol=symbol.lower(), SYMBOL=symbol.upper())
        # When OI_S3_BUCKET is set without template, use it as-is (single-bucket mode)
        return explicit
    template = os.environ.get("OI_S3_BUCKET_TEMPLATE", "{symbol}-oi")
    try:
        return template.format(symbol=symbol.lower(), SYMBOL=symbol.upper())
    except Exception:
        return default_s3_bucket(symbol)


def s3_upload(s3, bucket, key, fileobj_or_path, err_log: list, extra_args=None):
    if s3 is None:
        return False
    try:
        if hasattr(fileobj_or_path, "read"):
            kwargs = {"ExtraArgs": extra_args} if extra_args else {}
            s3.upload_fileobj(fileobj_or_path, bucket, key, **kwargs)
        else:
            kwargs = {"ExtraArgs": extra_args} if extra_args else {}
            s3.upload_file(fileobj_or_path, bucket, key, **kwargs)
        return True
    except (BotoCoreError, ClientError, NoCredentialsError, Exception) as e:
        msg = f"S3 upload skipped for s3://{bucket}/{key}: {e}"
        err_log.append(msg)
        print(msg)
        return False


def s3_download(s3, bucket, key, local_path, err_log: list):
    if s3 is None:
        return False
    try:
        s3.download_file(bucket, key, local_path)
        return True
    except (BotoCoreError, ClientError, NoCredentialsError, Exception) as e:
        msg = f"S3 download skipped for s3://{bucket}/{key}: {e}"
        err_log.append(msg)
        print(msg)
        return False


def run_symbol(
    symbol: str,
    price_override=None,
    vol_override=None,
    step_override=None,
    s3=None,
) -> None:
    """OCC → spot → IV → hedge-shock pivot for one underlying. Raises on hard failure."""
    err_log: list[str] = []
    symbol = symbol.strip().upper()
    default_price = DEFAULT_PRICES.get(symbol, 100.0)
    default_vol = DEFAULT_VOLS.get(symbol, 52.0)  # ~52% flat fallback if AlphaQuery scrape fails
    step = step_override if step_override is not None else 100.0
    deltas = build_deltas(step)

    os.makedirs(OUT_DIR, exist_ok=True)
    fname = os.path.join(OUT_DIR, f"{symbol}-{DATESTR}")
    index_html_path = os.path.join(OUT_DIR, f"{symbol}-index.html")
    so_csv_path = os.path.join(OUT_DIR, f"{symbol}-so.csv")
    index_csv_local = os.path.join(OUT_DIR, f"{symbol}-index.csv")

    occ_url = (
        f"https://marketdata.theocc.com/series-search?"
        f"symbolType=U&symbol={symbol}"
    )
    s3_bucket = resolve_bucket(symbol)

    curr_price, day_volume = fetch_spot(symbol, default_price, err_log)
    flatvol = fetch_iv(symbol, default_vol, err_log)

    if price_override is not None:
        curr_price = price_override
    if vol_override is not None:
        flatvol = vol_override

    if not os.path.isfile(fname):
        print(f"[{symbol}] Downloading open interest from OCC")
        try:
            download_with_headers(occ_url, fname)
        except Exception as e:
            raise RuntimeError(f"Error downloading OCC open interest for {symbol}: {e}") from e
    else:
        print(f"[{symbol}] Using cached OCC file {fname}")

    # Sanity: empty / non-TSV OCC response
    if os.path.getsize(fname) < 50:
        raise RuntimeError(f"OCC file for {symbol} looks empty ({fname})")

    print(
        f"[{symbol}] @ {curr_price} price, {flatvol} imp vol, "
        f"{step} point move (shocks={deltas}); s3_bucket={s3_bucket}"
    )

    df = pd.read_csv(fname, sep="\\t", engine="python", skiprows=6)
    if df.empty or "Integer" not in df.columns and "Strike" not in df.columns:
        # Re-read may fail if file was already cleaned CSV from a prior run today
        try:
            df2 = pd.read_csv(fname)
            if "Strike" in df2.columns and "Expiry" in df2.columns:
                df = df2
                if "Date" in df.columns:
                    df = df.drop(columns=["Date"])
            else:
                raise ValueError("unrecognized OCC schema")
        except Exception as e:
            raise RuntimeError(
                f"OCC parse failed for {symbol} (no series or bad file): {e}"
            ) from e
    else:
        drop_cols = [c for c in ["ProductSymbol", "C/P", "Position Limit"] if c in df.columns]
        df.drop(columns=drop_cols, inplace=True)
        df.reset_index(inplace=True)
        if "index" in df.columns:
            df.drop(columns=["index"], inplace=True)
        df.rename(columns={"Integer": "Strike"}, inplace=True)
        df["Strike"] = df["Strike"] + df["Dec"] / 1000
        df["Expiry"] = pd.to_datetime(df[["year", "Month", "Day"]])
        df["Expiry"] = df["Expiry"] + pd.DateOffset(days=1)
        df.drop(columns=["Dec", "year", "Month", "Day"], inplace=True)

        print(f"[{symbol}] Writing snapshot CSV (and S3/{s3_bucket} if configured)")
        df["Date"] = DATESTR
        df.to_csv(fname, header=True, index=False)
        with open(fname, "rb") as f:
            s3_upload(s3, s3_bucket, f"snapshot/{DATESTR}.csv", f, err_log)
        df.drop(columns=["Date"], inplace=True)

    df = df[df["Expiry"] >= datetime.today()]
    if df.empty:
        raise RuntimeError(f"No future-dated OCC options rows for {symbol}")

    df["DTE"] = df["Expiry"] - datetime(
        datetime.today().year, datetime.today().month, datetime.today().day
    )
    df["DTE"] = df["DTE"].dt.days

    conSum = pd.DataFrame()
    for shocks in deltas:
        price = shocks + curr_price
        work = df.copy()
        work["Greeks"] = work.apply(
            lambda x: greek_string([price, x["Strike"], RATE, x["DTE"]], flatvol),
            axis=1,
        )
        (
            work["callPrice"],
            work["putPrice"],
            work["callDelta"],
            work["putDelta"],
            work["callDelta2"],
            work["putDelta2"],
            work["callTheta"],
            work["putTheta"],
            work["callRho"],
            work["putRho"],
            work["vega"],
            work["gamma"],
        ) = zip(*work.pop("Greeks"))

        result = work[["Expiry", "Call", "Put", "callDelta", "putDelta"]].copy()
        result["callHedge"] = result["Call"] * result["callDelta"] * 100
        result["putHedge"] = result["Put"] * result["putDelta"] * 100
        result["netHedge"] = result["callHedge"] + result["putHedge"]
        sumByExpiry = result[
            ["Expiry", "callHedge", "putHedge", "netHedge"]
        ].groupby("Expiry").sum()
        sumByExpiry["Price"] = price
        conSum = pd.concat([conSum, sumByExpiry])

    conSum["ProcDate"] = DATESTR
    conSum["ClosePrice"] = curr_price
    summary_csv = fname + "-summary.csv"
    conSum.to_csv(summary_csv, header=True)
    with open(summary_csv, "rb") as f:
        s3_upload(s3, s3_bucket, f"summary/{DATESTR}-summary.csv", f, err_log)

    pivtable_expiry = pd.pivot_table(
        conSum.round(1),
        values=["netHedge"],
        index=["Expiry"],
        columns=["Price"],
        aggfunc=np.sum,
    )
    pivtable = pd.pivot_table(
        conSum, values=["netHedge"], columns=["Price"], aggfunc=np.sum
    )
    to_subtract = copy.copy(pivtable[curr_price])
    for shocks in deltas:
        price = shocks + curr_price
        if shocks != 0:
            pivtable[price] = pivtable[price] - to_subtract
    pivtable.columns = deltas

    summary_output = pd.DataFrame()
    summary_output["Date"] = [DATESTR]
    summary_output["Symbol"] = [symbol]
    summary_output["Price"] = [curr_price]
    summary_output["Volume"] = [day_volume]
    summary_output["IV"] = [flatvol]
    summary_output["key"] = [0]
    pivtable = pivtable.copy()
    pivtable["key"] = [0]
    summary_output = pd.merge(summary_output, pivtable, on="key")
    summary_output.drop(columns=["key"], inplace=True)

    print(summary_output)

    index_csv = pd.DataFrame(columns=summary_output.columns)
    if s3_download(s3, s3_bucket, "index.csv", index_csv_local, err_log):
        try:
            index_csv = pd.read_csv(index_csv_local)
            for col in summary_output.columns:
                if col not in index_csv.columns:
                    index_csv[col] = np.nan
            index_csv = index_csv[summary_output.columns]
        except Exception as e:
            err_log.append(f"could not read index.csv: {e}")
    elif os.path.isfile(index_csv_local):
        try:
            index_csv = pd.read_csv(index_csv_local)
            for col in summary_output.columns:
                if col not in index_csv.columns:
                    index_csv[col] = np.nan
            index_csv = index_csv[summary_output.columns]
        except Exception as e:
            err_log.append(f"could not read local index csv: {e}")

    combined = pd.concat([summary_output, index_csv], ignore_index=True)
    combined.drop_duplicates(subset=None, keep="first", inplace=True)

    summary_link = (
        f"https://{s3_bucket}.s3.amazonaws.com/summary/{DATESTR}-summary.csv"
    )
    err_msg = ("\n" + "\n".join(err_log) + "\n") if err_log else "\n"
    with open(index_html_path, "w") as fn:
        fn.write(f"By @generalenthu — {symbol} open interest hedge table")
        fn.write(
            "<br>Last updated at: "
            + datetime.today().strftime("%Y-%m-%d %H:%M:%S")
            + " EST"
        )
        fn.write(f"<br>Symbol: {symbol}")
        fn.write(
            "<br>Call vs Puts impact data "
            f'<a href="{summary_link}">here</a>. Pivot by the Price column.'
        )
    with open(index_html_path, "a") as fn:
        fn.write(
            "<br>"
            + pivtable_expiry.fillna(value=0).to_html(float_format="{0:,.0f}".format)
        )
        fn.write(
            "<br>"
            + combined.fillna(value=0).to_html(
                index=False, float_format="{0:,.0f}".format
            )
        )
        try:
            fn.write("<br>" + requests.get("https://api.ipify.org", timeout=5).text)
        except Exception:
            pass
        fn.write(err_msg)

    combined.to_csv(so_csv_path, index=False)
    combined.to_csv(index_csv_local, index=False)

    if symbol == "TSLA":
        with open("index.html", "w") as fn:
            fn.write(open(index_html_path).read())
        combined.to_csv("so.csv", index=False)
        combined.to_csv("index.csv", index=False)

    with open(index_html_path, "rb") as f:
        s3_upload(
            s3,
            s3_bucket,
            "index.html",
            f,
            err_log,
            extra_args={"ContentType": "text/html"},
        )
    with open(so_csv_path, "rb") as f:
        s3_upload(s3, s3_bucket, "index.csv", f, err_log)

    print(f"[{symbol}] Wrote local snapshot HTML: {index_html_path}")
    print(f"[{symbol}] Wrote local summary CSV: {summary_csv}")
    if err_log:
        print(f"[{symbol}] notes:\n" + "\n".join(err_log))


def main(argv=None):
    args = parse_args(argv)
    try:
        symbols = resolve_symbols(args)
    except SystemExit:
        raise
    except Exception as e:
        print(f"Failed to resolve symbols: {e}")
        sys.exit(2)

    # Deduplicate preserving order
    seen = set()
    uniq = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    symbols = uniq

    s3 = s3_client_or_none()
    if s3 is None:
        print("No AWS credentials; skipping S3 uploads/downloads")

    print(f"Running {len(symbols)} symbol(s): {', '.join(symbols)}")
    failures = []
    for symbol in symbols:
        try:
            run_symbol(
                symbol,
                price_override=args.price,
                vol_override=args.vol,
                step_override=args.step,
                s3=s3,
            )
        except Exception as e:
            failures.append((symbol, str(e)))
            print(f"[{symbol}] FAILED: {e}")
            traceback.print_exc()
            print(f"[{symbol}] continuing with remaining symbols…")

    print("\n=== batch summary ===")
    print(f"ok: {len(symbols) - len(failures)} / {len(symbols)}")
    if failures:
        for sym, err in failures:
            print(f"  FAIL {sym}: {err}")
        sys.exit(1 if len(failures) == len(symbols) else 0)


if __name__ == "__main__":
    main()
