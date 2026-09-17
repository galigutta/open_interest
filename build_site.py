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
    --tile:#0e1626;
    --sal: env(safe-area-inset-left, 0px); --sar: env(safe-area-inset-right, 0px);
    --sab: env(safe-area-inset-bottom, 0px);
  }
  * { box-sizing:border-box; }
  html { font-size: 16px; }
  body {
    margin:0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
    background: radial-gradient(1200px 600px at 10% -10%, #1a2744 0%, var(--bg) 55%);
    color: var(--text); font-size: 1rem; line-height:1.5;
    -webkit-text-size-adjust: 100%;
  }
  .wrap {
    max-width: 1100px; margin: 0 auto;
    padding: 32px max(20px, var(--sar)) calc(64px + var(--sab)) max(20px, var(--sal));
  }
  h1 { font-size: 1.6rem; margin: 0 0 8px; line-height:1.25; }
  h2 { font-size:1.15rem; margin:0 0 10px; }
  h3 { font-size:0.8rem; margin:18px 0 8px; color:var(--muted); text-transform:uppercase; letter-spacing:0.06em; }
  .meta { color: var(--muted); margin-bottom: 20px; }
  .card {
    background: var(--card); border:1px solid var(--line); border-radius:14px;
    padding:18px 18px 10px; margin-bottom:22px;
  }
  .badge {
    display:inline-block; background:#1e3158; color:var(--accent);
    border:1px solid #2c4678; border-radius:999px; padding:3px 10px;
    font-size:0.85rem; margin-right:8px;
  }
  /* Wide tables scroll inside .tscroll; .tbox paints a right-edge fade on top */
  .tbox { position:relative; margin: 0 -2px 8px; }
  .tscroll {
    overflow-x:auto; -webkit-overflow-scrolling: touch; overscroll-behavior-x: contain;
    border-radius: 10px;
  }
  .tbox::after {
    content:""; position:absolute; top:0; right:0; bottom:0; width:28px; pointer-events:none;
    background: linear-gradient(to right, rgba(18,26,43,0), var(--card));
    border-radius: 0 10px 10px 0;
  }
  table { border-collapse: separate; border-spacing:0; width:100%; font-variant-numeric: tabular-nums; }
  th, td { padding: 10px 12px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }
  th:first-child, td:first-child { text-align:left; }
  thead th { color: var(--muted); font-weight:600; font-size:0.85rem; position: sticky; top: 0; background: var(--card); z-index: 2; }
  /* Sticky label column with a divider so it separates while scrolling */
  th:first-child, td:first-child {
    position: sticky; left: 0; background: var(--card); z-index: 1;
    border-right: 1px solid var(--line); box-shadow: 6px 0 8px -6px rgba(0,0,0,0.6);
  }
  thead th:first-child { z-index: 3; }
  tbody tr:hover { background: rgba(110,168,254,0.06); }
  .note { color: var(--muted); font-size: 0.95rem; }
  a { color: var(--accent); }
  code { background:#0e1626; padding:1px 6px; border-radius:6px; }
  .symgrid { display:grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap:12px; margin:8px 0 8px; }
  .symcard {
    display:block; padding:14px 16px; border-radius:12px; border:1px solid var(--line);
    background:var(--tile); text-decoration:none; color:var(--text);
    min-height: 56px;
  }
  .symcard:hover { border-color: var(--accent); }
  .symcard .t { font-weight:700; font-size:1.05rem; }
  .symcard .s { color:var(--muted); font-size:0.85rem; margin-top:4px; }
  .symcard.has-data { border-color:#2c4678; background: linear-gradient(180deg, #15223c, var(--tile)); }
  .symcard.has-data .s { color: var(--text); opacity:0.85; }
  .symcard.empty { background:transparent; border-style:dashed; opacity:0.6; }
  .symcard.empty .t { font-weight:600; }
  .symcard.empty .s { font-size:0.8rem; }
  .muted { color: var(--muted); }
  .back { display:inline-flex; align-items:center; min-height:44px; padding:6px 0; text-decoration:none; }
  .num-pos { color: var(--pos); }
  .num-neg { color: var(--neg); }
  .scroll-hint { display:none; color:var(--accent); font-size:0.85rem; margin:0 0 8px; font-weight:600; }
  /* Latest-day spotlight */
  .spot-head { display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:4px 12px; margin-bottom:12px; }
  .spot-head h2 { margin:0; }
  .spot-date { color:var(--muted); font-size:0.95rem; }
  .tiles { display:grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap:10px; margin-bottom:10px; }
  .tile {
    background:var(--tile); border:1px solid var(--line); border-radius:12px;
    padding:12px 14px; min-height:72px; display:flex; flex-direction:column; justify-content:center;
  }
  .tile .k { color:var(--muted); font-size:0.8rem; }
  .tile .v { font-size:1.35rem; font-weight:700; font-variant-numeric: tabular-nums; line-height:1.2; }
  .tile .sub { color:var(--muted); font-size:0.78rem; font-variant-numeric: tabular-nums; }
  .tile.pos { border-color: rgba(61,214,140,0.35); background: linear-gradient(180deg, rgba(61,214,140,0.10), var(--tile)); }
  .tile.neg { border-color: rgba(255,123,114,0.35); background: linear-gradient(180deg, rgba(255,123,114,0.10), var(--tile)); }
  .tile.pos .v { color:var(--pos); }
  .tile.neg .v { color:var(--neg); }
  @media (max-width: 720px) {
    .wrap { padding: 16px max(14px, var(--sar)) calc(40px + var(--sab)) max(14px, var(--sal)); }
    h1 { font-size: 1.35rem; }
    h2 { font-size: 1.05rem; }
    .meta { font-size: 0.9rem; margin-bottom: 14px; }
    .card { padding: 14px 12px 8px; border-radius: 12px; margin-bottom: 16px; }
    .symgrid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap:10px; }
    .symcard { padding: 14px; min-height: 72px; }
    .symcard .t { font-size: 1.12rem; }
    .symcard.empty { min-height: 56px; }
    th, td { padding: 10px 10px; font-size: 0.9rem; }
    .scroll-hint { display:block; }
    .badge { margin-bottom: 6px; }
    .col-sym { display:none; }
    .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .tile.wide { grid-column: 1 / -1; }
    .tile .v { font-size:1.3rem; }
  }
  @media (max-width: 380px) {
    .symgrid { grid-template-columns: 1fr; }
  }
  @media (hover: none) {
    .symcard:active { border-color: var(--accent); background:#152038; }
    tbody tr:hover { background: transparent; }
  }
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


def fmt_compact(x) -> str:
    """Short signed share count for tiles, e.g. -111.4M."""
    try:
        if pd.isna(x):
            return "—"
        v = float(x)
    except Exception:
        return html.escape(str(x))
    sign = "+" if v > 0 else "−" if v < 0 else ""
    a = abs(v)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{sign}{a / div:,.1f}{suf}"
    return f"{sign}{a:,.0f}"


def sign_class(x) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if pd.isna(v) or v == 0:
        return ""
    return "num-pos" if v > 0 else "num-neg"


def shock_value(col) -> float | None:
    """Shock ladder columns are numeric point moves ('-100.0', '20.0', ...)."""
    try:
        return float(col)
    except (TypeError, ValueError):
        return None


def latest_row(df: pd.DataFrame) -> pd.Series | None:
    """Most recent row; among same-date rows prefer the one with the most filled values."""
    if df is None or df.empty:
        return None
    d = df.copy()
    d["_filled"] = d.notna().sum(axis=1)
    if "Date" in d.columns:
        d["_d"] = pd.to_datetime(d["Date"], errors="coerce")
        d = d.sort_values(["_d", "_filled"], ascending=False, kind="stable")
    else:
        d = d.sort_values("_filled", ascending=False, kind="stable")
    return d.iloc[0].drop(labels=[c for c in ("_d", "_filled") if c in d.columns])


def wrap_table(table_html: str) -> str:
    return f"<div class='tbox'><div class='tscroll'>{table_html}</div></div>"


def df_to_html_table(df: pd.DataFrame, shock_cols=True) -> str:
    if df is None or df.empty:
        return "<p class='note'>No history rows yet.</p>"
    show = df.copy()
    # Prefer readable column order
    preferred = ["Date", "Symbol", "Price", "Volume", "IV"]
    shock = [c for c in show.columns if c not in preferred]
    cols = [c for c in preferred if c in show.columns] + shock
    show = show[cols]

    def th(c):
        cls = " class='col-sym'" if c == "Symbol" else ""
        v = shock_value(c) if c in shock else None
        label = f"{v:+g}" if v not in (None, 0) else ("0" if v == 0 else str(c))
        return f"<th{cls}>{html.escape(label)}</th>"

    thead = "<tr>" + "".join(th(c) for c in show.columns) + "</tr>"
    rows = []
    for _, r in show.iterrows():
        cells = []
        for c in show.columns:
            val = r[c]
            if c == "Date":
                cells.append(f"<td>{html.escape(str(val))}</td>")
            elif c == "Symbol":
                cells.append(f"<td class='col-sym'>{html.escape(str(val))}</td>")
            elif c == "Volume":
                cells.append(f"<td>{fmt_num(val, 0)}</td>")
            elif c in ("Price", "IV"):
                cells.append(f"<td>{fmt_num(val, 2)}</td>")
            else:
                cls = sign_class(val) if shock_cols else ""
                attr = f" class='{cls}'" if cls else ""
                cells.append(f"<td{attr}>{fmt_num(val, 0)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return wrap_table(
        f"<table class='dataframe hist'><thead>{thead}</thead><tbody>{''.join(rows)}</tbody></table>"
    )


_PIVOT_TD = re.compile(r"<td>\s*(-?[\d,]+(?:\.\d+)?)\s*</td>")


def extract_expiry_table(symbol_index_html: Path) -> str:
    """Pull the first <table>...</table> from oi.py's snapshot HTML if present."""
    if not symbol_index_html.is_file():
        return ""
    text = symbol_index_html.read_text(errors="ignore")
    m = re.search(r"(<table\b.*?</table>)", text, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    table = re.sub(r'\sborder="1"', "", m.group(1))

    def color(mm):
        cls = sign_class(mm.group(1).replace(",", ""))
        return f"<td class='{cls}'>{mm.group(1)}</td>" if cls else mm.group(0)

    return wrap_table(_PIVOT_TD.sub(color, table))


def spotlight_html(row: pd.Series | None) -> str:
    """Large tiles for the latest day: spot, IV, and key shocks (real values only)."""
    if row is None:
        return ""
    shocks = {}
    for c in row.index:
        v = shock_value(c)
        if v is not None and not pd.isna(row[c]):
            shocks[v] = row[c]
    wanted = [k for k in (-50.0, -20.0, 0.0, 20.0, 50.0) if k in shocks]
    if not wanted:
        wanted = sorted(shocks)
    price = row.get("Price")
    has_price = price is not None and not pd.isna(price)

    tiles = []
    date = html.escape(str(row.get("Date", ""))) if "Date" in row.index else ""
    tiles.append(
        f"<div class='tile'><div class='k'>Spot</div><div class='v'>{fmt_num(price)}</div></div>"
    )
    iv = row.get("IV")
    iv_txt = f"{fmt_num(iv)}%" if iv is not None and not pd.isna(iv) else "—"
    tiles.append(f"<div class='tile'><div class='k'>IV (30d)</div><div class='v'>{iv_txt}</div></div>")
    for k in wanted:
        val = shocks[k]
        tone = {"num-pos": " pos", "num-neg": " neg"}.get(sign_class(val), "")
        label = "At spot (0)" if k == 0 else f"Shock {k:+g}"
        sub = f"@ {fmt_num(float(price) + k)}" if has_price else ""
        wide = " wide" if k == 0 else ""
        tiles.append(
            f"<div class='tile{tone}{wide}'><div class='k'>{label}</div>"
            f"<div class='v'>{fmt_compact(val)}</div><div class='sub'>{sub}</div></div>"
        )
    return f"""<div class="card">
      <div class="spot-head"><h2>Latest day</h2><span class="spot-date">{date}</span></div>
      <div class="tiles">{''.join(tiles)}</div>
      <p class="note">Net hedge shares if spot moves by the shown points. Green = positive, red = negative.</p>
    </div>"""


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
    # Count distinct dates, not rows (oi.py can append several rows per day)
    if df.empty:
        days = 0
    elif "Date" in df.columns:
        days = int(df["Date"].nunique())
    else:
        days = len(df)
    price = fmt_num(row["Price"]) if row is not None and "Price" in row.index else "—"
    iv = fmt_num(row["IV"]) if row is not None and "IV" in row.index else "—"
    expiry_html = extract_expiry_table(snap_html)
    hist_html = df_to_html_table(df)
    spot_html = spotlight_html(row)

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
<title>{html.escape(symbol)} — Options OI hedge table</title>
<style>{CSS}</style>
</head>
<body>
  <div class="wrap">
    <p class="meta"><a class="back" href="../../index.html">← All symbols</a></p>
    <h1>{html.escape(symbol)} open interest hedge table</h1>
    <div class="meta">
      <span class="badge">{days} trading day(s) with OCC OI</span>
      Last updated: {html.escape(now_label)} · Spot {price} · IV {iv}%
    </div>
    {spot_html}
    <div class="card">
      <h2>Historical summary (net hedge shares by price shock)</h2>
      <p class="note">Columns like <code>-100 … +100</code> are net dealer-style hedge share changes vs the spot shock ladder.
      OI from OCC; <strong>spot from yfinance</strong>; <strong>IV from AlphaQuery 30-day IV mean</strong> (flat ~52% fallback if scrape fails).</p>
      <p class="scroll-hint">Swipe sideways to see all shock columns →</p>
      {hist_html}
    </div>
    <div class="card">
      <h2>Latest expiry × price netHedge pivot</h2>
      <p class="note">From the most recent OCC snapshot run for {html.escape(symbol)}.</p>
      {"<p class='scroll-hint'>Swipe sideways to see all price columns →</p>" if expiry_html else ""}
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
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
<title>{s} — pending</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<p class="meta"><a class="back" href="../../index.html">← All symbols</a></p>
<h1>{s}</h1>
<p class="note">No OCC snapshot yet. Run <code>python oi.py {s}</code> then rebuild the site.</p>
</div></body></html>
""")
    for c in cards:
        if c["symbol"] not in {x["symbol"] for x in ordered}:
            ordered.append(c)

    # Symbols with data first; watchlist order preserved within each group
    ordered.sort(key=lambda c: not c["has_data"])
    with_data = sum(1 for c in ordered if c["has_data"])

    def card(c):
        if c["has_data"]:
            status, cls = f"{c['days']} day(s) · px {c['price']} · IV {c['iv']}", "symcard has-data"
        else:
            status, cls = "no data yet", "symcard empty"
        return (
            f'<a class="{cls}" href="{html.escape(c["href"])}">'
            f'<div class="t">{html.escape(c["symbol"])}</div>'
            f'<div class="s">{html.escape(status)}</div></a>'
        )

    live = [card(c) for c in ordered if c["has_data"]]
    pending = [card(c) for c in ordered if not c["has_data"]]
    grid = []
    if live:
        grid.append(f'<h3>With data</h3><div class="symgrid">{"".join(live)}</div>')
    if pending:
        grid.append(f'<h3>Awaiting snapshot</h3><div class="symgrid">{"".join(pending)}</div>')

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
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
      {''.join(grid)}
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
