#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Open-interest hedge table generator.

Usage:
  python oi.py [SYMBOL] [price] [vol] [step]

SYMBOL defaults to TSLA. If the first argument is numeric, it is treated as
price (legacy TSLA-only CLI: python oi.py price volatility step).
"""

import copy
import os
import re
import sys
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

out_dir = "snapshot"
datestr = date.today().strftime("%Y-%m-%d")
err_msg = "\n"
rate = 2.0
nyse = mcal.get_calendar("NYSE")
DEFAULT_BUCKETS = {
    "TSLA": "tsla-oi",
    "SPCX": "spcx-oi",
}
DEFAULT_PRICES = {
    "TSLA": 770.0,
    "SPCX": 150.0,
}
DEFAULT_VOLS = {
    "TSLA": 55.0,
    "SPCX": 52.0,
}


def usage():
    print("usage: python oi.py [SYMBOL] [price] [vol] [step]")
    print("  SYMBOL defaults to TSLA; e.g. python oi.py SPCX")
    print("  Legacy: python oi.py price volatility step  (implies TSLA)")


def parse_args(argv):
    """Return (symbol, price_override, vol_override, step_override)."""
    symbol = "TSLA"
    price_override = None
    vol_override = None
    step_override = None
    args = argv[1:]
    if not args:
        return symbol, price_override, vol_override, step_override

    # Legacy: first arg is numeric price → TSLA with price/vol/step
    try:
        float(args[0])
        legacy = True
    except ValueError:
        legacy = False

    if legacy:
        try:
            price_override = float(args[0])
            if len(args) > 1:
                vol_override = float(args[1])
            if len(args) > 2:
                step_override = float(args[2])
        except ValueError:
            usage()
            sys.exit(1)
        return symbol, price_override, vol_override, step_override

    symbol = args[0].strip().upper()
    try:
        if len(args) > 1:
            price_override = float(args[1])
        if len(args) > 2:
            vol_override = float(args[2])
        if len(args) > 3:
            step_override = float(args[3])
    except ValueError:
        usage()
        sys.exit(1)
    return symbol, price_override, vol_override, step_override


def build_deltas(step):
    """Match historical shape for step=100: [-100,-50,-20,0,20,50,100]."""
    step = float(step)
    return [-step, -step / 2.0, -step / 5.0, 0.0, step / 5.0, step / 2.0, step]


def greek_string(deets, iv):
    # deets: [underlyingPrice, strikePrice, interestRate, daysToExpiration]
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


def fetch_spot(symbol, default_price):
    """Prefer yfinance fast_info / history; fall back to info then default."""
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
        if now > now.replace(hour=16) and nyse.valid_days(
            start_date=datestr, end_date=datestr
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
        global err_msg
        err_msg += f"unable to get price from yahoo for {symbol}, defaulting price\n"
        print(e)
    return curr_price, day_volume


def fetch_iv(symbol, default_vol):
    """Scrape alphaquery 30-day IV mean; fall back to default_vol and log."""
    global err_msg
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
            # Fallback: parse prose value e.g. "... of <strong>0.5194</strong>"
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
        err_msg += (
            f"unable to get vol from alphaquery for {symbol}, "
            f"defaulting vol={default_vol}: {e}\n"
        )
        print(err_msg.strip())
    return flatvol


def s3_client_or_none():
    """Return an S3 client if credentials appear available, else None."""
    try:
        session = boto3.Session()
        creds = session.get_credentials()
        if creds is None:
            return None
        return session.client("s3")
    except Exception as e:
        print(f"S3 unavailable ({e}); continuing with local snapshots only")
        return None


def s3_upload(s3, bucket, key, fileobj_or_path, extra_args=None):
    global err_msg
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
        msg = f"S3 upload skipped for s3://{bucket}/{key}: {e}\n"
        err_msg += msg
        print(msg.strip())
        return False


def s3_download(s3, bucket, key, local_path):
    global err_msg
    if s3 is None:
        return False
    try:
        s3.download_file(bucket, key, local_path)
        return True
    except (BotoCoreError, ClientError, NoCredentialsError, Exception) as e:
        msg = f"S3 download skipped for s3://{bucket}/{key}: {e}\n"
        err_msg += msg
        print(msg.strip())
        return False


def main():
    global err_msg

    symbol, price_override, vol_override, step_override = parse_args(sys.argv)
    default_price = DEFAULT_PRICES.get(symbol, 100.0)
    default_vol = DEFAULT_VOLS.get(symbol, 50.0)
    step = step_override if step_override is not None else 100.0
    deltas = build_deltas(step)

    os.makedirs(out_dir, exist_ok=True)
    # Per-symbol cache so TSLA and SPCX snapshots do not collide
    fname = os.path.join(out_dir, f"{symbol}-{datestr}")
    index_html_path = os.path.join(out_dir, f"{symbol}-index.html")
    so_csv_path = os.path.join(out_dir, f"{symbol}-so.csv")
    index_csv_local = os.path.join(out_dir, f"{symbol}-index.csv")

    occ_url = (
        f"https://marketdata.theocc.com/series-search?"
        f"symbolType=U&symbol={symbol}"
    )
    s3_bucket = os.environ.get(
        "OI_S3_BUCKET", DEFAULT_BUCKETS.get(symbol, f"{symbol.lower()}-oi")
    )
    s3 = s3_client_or_none()
    if s3 is None:
        print("No AWS credentials; skipping S3 uploads/downloads")

    curr_price, day_volume = fetch_spot(symbol, default_price)
    flatvol = fetch_iv(symbol, default_vol)

    if price_override is not None:
        curr_price = price_override
    if vol_override is not None:
        flatvol = vol_override

    if not os.path.isfile(fname):
        print(f"Downloading open interest file for {symbol}")
        try:
            download_with_headers(occ_url, fname)
        except Exception as e:
            print(f"Error downloading file: {e}")
            err_msg += f"Error downloading open interest file: {e}\n"
            print(err_msg)
            sys.exit(1)
    else:
        print(f"Using cached OCC file {fname}")

    print(
        f"Using {symbol} @ {curr_price} price, {flatvol} imp vol, "
        f"{step} point move (shocks={deltas}):"
    )

    # Data wrangling to clean up the raw OCC TSV
    df = pd.read_csv(fname, sep="\\t", engine="python", skiprows=6)
    drop_cols = [c for c in ["ProductSymbol", "C/P", "Position Limit"] if c in df.columns]
    df.drop(columns=drop_cols, inplace=True)
    df.reset_index(inplace=True)
    if "index" in df.columns:
        df.drop(columns=["index"], inplace=True)
    df.rename(columns={"Integer": "Strike"}, inplace=True)
    df["Strike"] = df["Strike"] + df["Dec"] / 1000
    df["Expiry"] = pd.to_datetime(df[["year", "Month", "Day"]])
    # Add a day to expiry to prevent zero days to expiry on Fridays
    df["Expiry"] = df["Expiry"] + pd.DateOffset(days=1)
    df.drop(columns=["Dec", "year", "Month", "Day"], inplace=True)

    print(f"Copying snapshot CSV locally (and to S3/{s3_bucket} if configured)")
    df["Date"] = datestr
    df.to_csv(fname, header=True, index=False)
    with open(fname, "rb") as f:
        s3_upload(s3, s3_bucket, f"snapshot/{datestr}.csv", f)
    df.drop(columns=["Date"], inplace=True)

    df = df[df["Expiry"] >= datetime.today()]
    df["DTE"] = df["Expiry"] - datetime(
        datetime.today().year, datetime.today().month, datetime.today().day
    )
    df["DTE"] = df["DTE"].dt.days

    conSum = pd.DataFrame()
    for shocks in deltas:
        price = shocks + curr_price
        work = df.copy()
        work["Greeks"] = work.apply(
            lambda x: greek_string([price, x["Strike"], rate, x["DTE"]], flatvol),
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

    conSum["ProcDate"] = datestr
    conSum["ClosePrice"] = curr_price
    summary_csv = fname + "-summary.csv"
    conSum.to_csv(summary_csv, header=True)
    with open(summary_csv, "rb") as f:
        s3_upload(s3, s3_bucket, f"summary/{datestr}-summary.csv", f)

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
    summary_output["Date"] = [datestr]
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

    # Historical index: pull from S3 if possible, else local, else empty
    index_csv = pd.DataFrame(columns=summary_output.columns)
    if s3_download(s3, s3_bucket, "index.csv", index_csv_local):
        try:
            index_csv = pd.read_csv(index_csv_local)
            # Align columns if older files lack Symbol
            for col in summary_output.columns:
                if col not in index_csv.columns:
                    index_csv[col] = np.nan
            index_csv = index_csv[summary_output.columns]
        except Exception as e:
            err_msg += f"could not read index.csv: {e}\n"
    elif os.path.isfile(index_csv_local):
        try:
            index_csv = pd.read_csv(index_csv_local)
            for col in summary_output.columns:
                if col not in index_csv.columns:
                    index_csv[col] = np.nan
            index_csv = index_csv[summary_output.columns]
        except Exception as e:
            err_msg += f"could not read local index csv: {e}\n"

    combined = pd.concat([summary_output, index_csv], ignore_index=True)
    combined.drop_duplicates(subset=None, keep="first", inplace=True)

    summary_link = (
        f"https://{s3_bucket}.s3.amazonaws.com/summary/{datestr}-summary.csv"
    )
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

    # Also write root-level names for TSLA backward compatibility with Dockerfile/S3 site
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
            extra_args={"ContentType": "text/html"},
        )
    with open(so_csv_path, "rb") as f:
        s3_upload(s3, s3_bucket, "index.csv", f)

    print(f"Wrote local snapshot HTML: {index_html_path}")
    print(f"Wrote local summary CSV: {summary_csv}")
    print(err_msg)


if __name__ == "__main__":
    main()
