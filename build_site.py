#!/usr/bin/env python3
"""Build a multi-ticker Options OI hedge tables static site from snapshot/."""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

DEFAULT_WATCHLIST = [
    "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META",
    "AMD", "MU", "INTC", "NFLX", "SPCX",
]

CSS = """
  :root {
    --bg:#0b1220; --card:#121a2b; --text:#e8eefc; --muted:#9bb0d0;
    --accent:#6ea8fe; --pos:#3dd68c; --neg:#ff7b72; --line:#243049;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
    background: radial-gradient(1200px 600px at 10% -10%, #1a2744 0%, var(--bg) 55%);
    color: var(--text); line-height:1.45;
  }
  .wrap { max-width: 1100px; margin: 0 auto; padding: 32px 20px 64px; }
  h1 { font-size: 1.6rem; margin: 0 0 8px; }
  h2 { font-size:1.15rem; margin:0 0 10px; }
  .meta { color: var(--muted); margin-bottom: 20px; }
  .card {
    background: var(--card); border:1px solid var(--line); border-radius:14px;
    padding:18px 18px 8px; margin-bottom:22px; overflow-x:auto;
  }
  .badge {
    display:inline-block; background:#1e3158; color:var(--accent);
    border:1px solid #2c4678; border-radius:999px; padding:3px 10px;
    font-size:0.85rem; margin-right:8px;
  }
  table { border-collapse: collapse; width:100%; font-variant-numeric: tabular-nums; }
  th, td { padding: 10px 12px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }
  th:first-child, td:first-child { text-align:left; }
  thead th { color: var(--muted); font-weight:600; font-size:0.85rem; }
  tbody tr:hover { background: rgba(110,168,254,0.06); }
  .note { color: var(--muted); font-size: 0.95rem; }
  a { color: var(--accent); }
  code { background:#0e1626; padding:1px 6px; border-radius:6px; }
  .symgrid { display:grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap:12px; margin:12px 0 8px; }
  .symcard {
    display:block; padding:14px 16px; border-radius:12px; border:1px solid var(--line);
    background:#0e1626; text-decoration:none; color:var(--text);
  }
  .symcard:hover { border-color: var(--accent); }
  .symcard .t { font-weight:700; font-size:1.05rem; }
  .symcard .s { color:var(--muted); font-size:0.85rem; margin-top:4px; }
  .muted { color: var(--muted); }
"""


def load_watchlist(snapshot: Path, watchlist_file: Path | None) -> list[str]:
    symbols: list[str] = []
    if watchlist_file and watchlist_file.is_file():
        for line in watchlist_file.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                symbols.append(line.upper())
    # Also discover any snapshot/*-index.csv present
    found = sorted(
        p.name.split("-index.csv")[0]
        for p in snapshot.glob("*-index.csv")
        if p.name.endswith("-index.csv")
    )
    for s in found:
        if s not in symbols:
            symbols.append(s)
    if not symbols:
        symbols = list(DEFAULT_WATCHLIST)
    # Dedupe preserve order
    out, seen = [], set()
    for s in symbols:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def fmt_num(x, decimals=2):
    try:
        if pd.isna(x):
            return "—"
        v = float(x)
        if abs(v) >= 1000:
            return f"{v:,.0f}"
        return f"{v:,.{decimals}f}"
    except Exception:
        return html.escape(str(x))


def latest_row(df: pd.DataFrame) -> pd.Series | None:
    if df is None or df.empty:
        return None
    if "Date" in df.columns:
        d = df.copy()
        d["_d"] = pd.to_datetime(d["Date"], errors="coerce")
        d = d.sort_values("_d", ascending=False)
        return d.iloc[0]
    return df.iloc[0]


def df_to_html_table(df: pd.DataFrame, shock_cols=True) -> str:
    if df is None or df.empty:
        return "<p class='note'>No history rows yet.</p>"
    show = df.copy()
    # Prefer readable column order
    preferred = ["Date", "Symbol", "Price", "Volume", "IV"]
    shock = [c for c in show.columns if c not in preferred]
    cols = [c for c in preferred if c in show.columns] + shock
    show = show[cols]
    thead = "<tr>" + "".join(f"<th>{html.escape(str(c))}</th>" for c in show.columns) + "</tr>"
    rows = []
    for _, r in show.iterrows():
        cells = []
        for c in show.columns:
            val = r[c]
            if c in ("Date", "Symbol"):
                cells.append(f"<td>{html.escape(str(val))}</td>")
            elif c == "Volume":
                cells.append(f"<td>{fmt_num(val, 0)}</td>")
            elif c in ("Price", "IV"):
                cells.append(f"<td>{fmt_num(val, 2)}</td>")
            else:
                cells.append(f"<td>{fmt_num(val, 0)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table class='dataframe hist'><thead>{thead}</thead><tbody>{''.join(rows)}</tbody></table>"


def extract_expiry_table(symbol_index_html: Path) -> str:
    """Pull the first <table>...</table> from oi.py's snapshot HTML if present."""
    if not symbol_index_html.is_file():
        return ""
    text = symbol_index_html.read_text(errors="ignore")
    m = re.search(r"(<table\b.*?</table>)", text, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return m.group(1)


def write_symbol_page(out_dir: Path, symbol: str, index_csv: Path, snap_html: Path, now_label: str):
    sym_dir = out_dir / "symbols" / symbol
    sym_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(index_csv) if index_csv.is_file() else pd.DataFrame()
    # Copy artifacts
    if index_csv.is_file():
        shutil.copy2(index_csv, sym_dir / "index.csv")
    # Copy latest summary / oi if present next to index
    snap = index_csv.parent
    for p in snap.glob(f"{symbol}-*-summary.csv"):
        shutil.copy2(p, sym_dir / p.name)
    for p in snap.glob(f"{symbol}-????-??-??"):
        if p.is_file() and not p.name.endswith("-summary.csv"):
            shutil.copy2(p, sym_dir / f"oi-{p.name.split('-', 1)[1]}.csv")

    row = latest_row(df)
    days = len(df) if not df.empty else 0
    price = fmt_num(row["Price"]) if row is not None and "Price" in row.index else "—"
    iv = fmt_num(row["IV"]) if row is not None and "IV" in row.index else "—"
    expiry_html = extract_expiry_table(snap_html)
    hist_html = df_to_html_table(df)

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(symbol)} — Options OI hedge table</title>
<style>{CSS}</style>
</head>
<body>
  <div class="wrap">
    <p class="meta"><a href="../../index.html">← All symbols</a></p>
    <h1>{html.escape(symbol)} open interest hedge table</h1>
    <div class="meta">
      <span class="badge">{days} trading day(s) with OCC OI</span>
      Last updated: {html.escape(now_label)} · Spot {price} · IV {iv}%
    </div>
    <div class="card">
      <h2>Historical summary (net hedge shares by price shock)</h2>
      <p class="note">Columns like <code>-100 … +100</code> are net dealer-style hedge share changes vs the spot shock ladder.
      OI from OCC; <strong>spot from yfinance</strong>; <strong>IV from AlphaQuery 30-day IV mean</strong> (flat ~52% fallback if scrape fails).</p>
      {hist_html}
    </div>
    <div class="card">
      <h2>Latest expiry × price netHedge pivot</h2>
      <p class="note">From the most recent OCC snapshot run for {html.escape(symbol)}.</p>
      {expiry_html if expiry_html else "<p class='note'>Expiry pivot not available yet — re-run <code>python oi.py " + html.escape(symbol) + "</code>.</p>"}
    </div>
    <p class="note">Method: <a href="https://github.com/galigutta/open_interest">galigutta/open_interest</a></p>
  </div>
</body>
</html>
"""
    (sym_dir / "index.html").write_text(page)
    return {
        "symbol": symbol,
        "days": days,
        "price": price,
        "iv": iv,
        "has_data": index_csv.is_file() and not df.empty,
        "href": f"symbols/{symbol}/index.html",
    }


def write_index(out_dir: Path, cards: list[dict], now_label: str, watchlist: list[str]):
    # Ensure watchlist symbols appear even without data
    by_sym = {c["symbol"]: c for c in cards}
    ordered = []
    for s in watchlist:
        if s in by_sym:
            ordered.append(by_sym[s])
        else:
            ordered.append({
                "symbol": s, "days": 0, "price": "—", "iv": "—",
                "has_data": False, "href": f"symbols/{s}/index.html",
            })
            # stub page
            stub = out_dir / "symbols" / s
            stub.mkdir(parents=True, exist_ok=True)
            (stub / "index.html").write_text(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>{s} — pending</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<p class="meta"><a href="../../index.html">← All symbols</a></p>
<h1>{s}</h1>
<p class="note">No OCC snapshot yet. Run <code>python oi.py {s}</code> then rebuild the site.</p>
</div></body></html>
""")
    for c in cards:
        if c["symbol"] not in {x["symbol"] for x in ordered}:
            ordered.append(c)

    with_data = sum(1 for c in ordered if c["has_data"])
    grid = []
    for c in ordered:
        status = f"{c['days']} day(s) · px {c['price']} · IV {c['iv']}" if c["has_data"] else "no data yet"
        grid.append(
            f'<a class="symcard" href="{html.escape(c["href"])}">'
            f'<div class="t">{html.escape(c["symbol"])}</div>'
            f'<div class="s">{html.escape(status)}</div></a>'
        )

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Options OI hedge tables</title>
<style>{CSS}</style>
</head>
<body>
  <div class="wrap">
    <h1>Options OI hedge tables</h1>
    <div class="meta">
      <span class="badge">{with_data} / {len(ordered)} symbols with OCC rows</span>
      Last updated: {html.escape(now_label)} · Single-stock watchlist
      (NVDA, TSLA, AAPL, MSFT, AMZN, META, AMD, MU, INTC, NFLX, SPCX)
    </div>
    <div class="card">
      <h2>Symbols</h2>
      <p class="note">Each page shows the rolling net-hedge summary and latest expiry×shock pivot.
      Pipeline: OCC open interest → <strong>yfinance spot</strong> → <strong>AlphaQuery 30-day IV mean</strong> (flat ~52% if scrape fails) → BS hedge shocks.
      Code: <a href="https://github.com/galigutta/open_interest">galigutta/open_interest</a>.</p>
      <div class="symgrid">
        {''.join(grid)}
      </div>
    </div>
  </div>
</body>
</html>
"""
    (out_dir / "index.html").write_text(page)


def main():
    ap = argparse.ArgumentParser(description="Build multi-ticker OI hedge static site")
    ap.add_argument("--snapshot", default="snapshot", help="oi.py snapshot directory")
    ap.add_argument("--out", required=True, help="Output site directory (e.g. ../spcx-oi-site)")
    ap.add_argument("--watchlist", default="watchlist.txt", help="Watchlist file")
    args = ap.parse_args()

    snapshot = Path(args.snapshot)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "symbols").mkdir(exist_ok=True)

    watchlist = load_watchlist(snapshot, Path(args.watchlist) if args.watchlist else None)
    now_label = datetime.now().strftime("%Y-%m-%d %H:%M:%S ET")

    cards = []
    for sym in watchlist:
        index_csv = snapshot / f"{sym}-index.csv"
        snap_html = snapshot / f"{sym}-index.html"
        if index_csv.is_file():
            cards.append(write_symbol_page(out_dir, sym, index_csv, snap_html, now_label))
        else:
            # still create stub via write_index
            pass

    # Also pick up any extra symbols that have data but aren't on watchlist
    for index_csv in sorted(snapshot.glob("*-index.csv")):
        sym = index_csv.name[: -len("-index.csv")]
        if sym not in {c["symbol"] for c in cards}:
            cards.append(
                write_symbol_page(
                    out_dir, sym, index_csv, snapshot / f"{sym}-index.html", now_label
                )
            )

    write_index(out_dir, cards, now_label, watchlist)
    # Preserve legacy root CSV copies for SPCX if present (gh-pages consumers)
    spcx_csv = snapshot / "SPCX-index.csv"
    if spcx_csv.is_file():
        shutil.copy2(spcx_csv, out_dir / "index.csv")
    print(f"Wrote site to {out_dir} ({len(cards)} symbol page(s) with data)")


if __name__ == "__main__":
    main()
