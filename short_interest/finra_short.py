"""Find stocks that are getting shorted more and more, using FINRA data.

Two FINRA datasets are used:

1. Equity short interest (published twice a month).
   Brokers report how many shares are held short as of a settlement date
   around the 15th and at the end of each month. This is the "real" short
   position number. FINRA publishes it about 8 business days after the
   settlement date, so the newest figure is always ~2 weeks old.

2. Daily short sale volume (Reg SHO files).
   For every trading day: how many of the shares that traded were sold short.
   It updates daily, but it is noisy -- market makers sell short all day just
   to fill customer buy orders -- so treat it as a supporting signal, not as
   a count of open short positions.

Usage:
    python finra_short.py                         # last month, default filters
    python finra_short.py --top 50                # show more rows
    python finra_short.py --min-short-shares 500000 --min-avg-volume 100000

Results are written to ./output and raw downloads are cached in ./data.
"""

import argparse
import io
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

SHORT_INTEREST_URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
TOKEN_URL = "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token?grant_type=client_credentials"
SHORT_VOLUME_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{day:%Y%m%d}.txt"

PAGE_SIZE = 5000  # max rows FINRA's API returns per request

SHORT_INTEREST_FIELDS = [
    "settlementDate",
    "symbolCode",
    "issueName",
    "marketClassCode",
    "currentShortPositionQuantity",
    "averageDailyVolumeQuantity",
    "daysToCoverQuantity",
    "stockSplitFlag",
]

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------

def make_session():
    """HTTP session for FINRA's API.

    Public datasets work without an account. If FINRA_API_KEY and
    FINRA_API_SECRET are set (free account at developer.finra.org), they are
    used to get higher rate limits.
    """
    session = requests.Session()
    session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    key, secret = os.environ.get("FINRA_API_KEY"), os.environ.get("FINRA_API_SECRET")
    if key and secret:
        resp = requests.post(TOKEN_URL, auth=(key, secret), timeout=30)
        resp.raise_for_status()
        session.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return session


def query_api(session, body):
    """POST a query to the short interest dataset and return a list of rows."""
    resp = session.post(SHORT_INTEREST_URL, json=body, timeout=120)
    resp.raise_for_status()
    # FINRA answers "no rows" with an empty body (HTTP 204).
    return resp.json() if resp.text.strip() else []


def recent_settlement_dates(session, count, as_of):
    """Return the `count` most recent settlement dates on or before as_of, newest first."""
    dates = []
    end = as_of
    for _ in range(count):
        rows = query_api(session, {
            "limit": 1,
            "fields": ["settlementDate"],
            "sortFields": ["-settlementDate"],
            "dateRangeFilters": [{
                "fieldName": "settlementDate",
                "startDate": (end - timedelta(days=60)).isoformat(),
                "endDate": end.isoformat(),
            }],
        })
        if not rows:
            break
        found = date.fromisoformat(rows[0]["settlementDate"][:10])
        dates.append(found)
        end = found - timedelta(days=1)
    return dates


def fetch_short_interest(session, settlement_date, cache_dir):
    """All short interest rows for one settlement date (cached as CSV)."""
    cache_file = cache_dir / f"short_interest_{settlement_date.isoformat()}.csv"
    if cache_file.exists():
        return pd.read_csv(cache_file)

    rows, offset = [], 0
    while True:
        page = query_api(session, {
            "limit": PAGE_SIZE,
            "offset": offset,
            "fields": SHORT_INTEREST_FIELDS,
            "compareFilters": [{
                "compareType": "EQUAL",
                "fieldName": "settlementDate",
                "fieldValue": settlement_date.isoformat(),
            }],
        })
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    df = pd.DataFrame(rows, columns=SHORT_INTEREST_FIELDS)
    df.to_csv(cache_file, index=False)
    return df


def parse_short_volume(text):
    """Parse one Reg SHO daily file (pipe-delimited) into a DataFrame."""
    df = pd.read_csv(io.StringIO(text), sep="|", dtype={"Symbol": str})
    for col in ["ShortVolume", "ShortExemptVolume", "TotalVolume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # Some files end with a trailer line that is not a real record.
    df = df.dropna(subset=["Symbol", "ShortVolume", "TotalVolume"])
    df["Date"] = pd.to_datetime(df["Date"].astype(str).str[:8], format="%Y%m%d")
    return df[["Date", "Symbol", "ShortVolume", "TotalVolume"]]


def fetch_short_volume(start, end, cache_dir):
    """Daily short sale volume for every weekday between start and end."""
    frames = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            cache_file = cache_dir / f"short_volume_{day:%Y%m%d}.txt"
            if cache_file.exists():
                text = cache_file.read_text()
            else:
                resp = requests.get(SHORT_VOLUME_URL.format(day=day), timeout=60)
                # Holidays have no file; the CDN answers 403 or 404.
                text = resp.text if resp.status_code == 200 else ""
                if text:
                    cache_file.write_text(text)
            if text:
                frames.append(parse_short_volume(text))
        day += timedelta(days=1)
    if not frames:
        return pd.DataFrame(columns=["Date", "Symbol", "ShortVolume", "TotalVolume"])
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def tidy_short_interest(raw):
    """Rename FINRA's columns and fix types."""
    df = raw.rename(columns={
        "settlementDate": "date",
        "symbolCode": "symbol",
        "issueName": "name",
        "marketClassCode": "market",
        "currentShortPositionQuantity": "short_shares",
        "averageDailyVolumeQuantity": "avg_daily_volume",
        "daysToCoverQuantity": "days_to_cover",
        "stockSplitFlag": "split",
    })
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for col in ["short_shares", "avg_daily_volume", "days_to_cover"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["split"] = df["split"].notna() & (df["split"].astype(str).str.strip() != "")
    df = df.dropna(subset=["symbol"])
    return df.drop_duplicates(subset=["date", "symbol"], keep="last")


def analyze_short_interest(si, min_short_shares, min_avg_volume, exclude_markets=()):
    """One row per symbol: short interest at each date and how much it grew.

    `si` is the tidy short interest table covering several settlement dates.
    Symbols with a stock split during the window are dropped, because a split
    multiplies the share count and looks like a giant jump in short interest.
    """
    dates = sorted(si["date"].unique())
    first, last = dates[0], dates[-1]

    shares = si.pivot(index="symbol", columns="date", values="short_shares")
    latest = si[si["date"] == last].set_index("symbol")
    earliest = si[si["date"] == first].set_index("symbol")
    had_split = si.groupby("symbol")["split"].any()

    out = pd.DataFrame(index=shares.index)
    out["name"] = latest["name"]
    out["market"] = latest["market"]
    for d in dates:
        out[f"short_{d.isoformat()}"] = shares[d]
    out["change_shares"] = shares[last] - shares[first]
    out["change_pct"] = out["change_shares"] / shares[first] * 100
    # True when short interest went up at every single report in the window.
    out["rising_every_report"] = (shares.diff(axis=1).iloc[:, 1:] > 0).all(axis=1)
    out["days_to_cover"] = latest["days_to_cover"]
    out["days_to_cover_change"] = latest["days_to_cover"] - earliest["days_to_cover"]
    out["avg_daily_volume"] = latest["avg_daily_volume"]

    keep = (
        shares[first].gt(0)
        & shares[last].ge(min_short_shares)
        & out["avg_daily_volume"].ge(min_avg_volume)
        & ~had_split.reindex(out.index, fill_value=False)
        & ~out["market"].isin(list(exclude_markets))
    )
    return out[keep].reset_index()


def analyze_short_volume(sv, min_avg_volume):
    """Compare the short-volume ratio in the first vs second half of the window.

    short ratio = shares sold short / all shares traded.
    A rising ratio means a growing share of trading is short selling.
    """
    days = sorted(sv["Date"].unique())
    if len(days) < 2:
        return pd.DataFrame()
    second_half_start = days[len(days) // 2]
    sv = sv.assign(half=sv["Date"].ge(second_half_start).map({False: "early", True: "late"}))

    totals = sv.groupby(["Symbol", "half"])[["ShortVolume", "TotalVolume"]].sum().unstack("half")
    trading_days = sv.groupby("Symbol")["Date"].nunique()

    out = pd.DataFrame(index=totals.index)
    out["short_ratio_early"] = totals[("ShortVolume", "early")] / totals[("TotalVolume", "early")]
    out["short_ratio_late"] = totals[("ShortVolume", "late")] / totals[("TotalVolume", "late")]
    out["short_ratio_change_pts"] = (out["short_ratio_late"] - out["short_ratio_early"]) * 100
    out["avg_daily_volume"] = totals["TotalVolume"].sum(axis=1) / trading_days

    keep = out["avg_daily_volume"].ge(min_avg_volume) & out["short_ratio_change_pts"].notna()
    out = out[keep]
    out[["short_ratio_early", "short_ratio_late"]] = out[["short_ratio_early", "short_ratio_late"]] * 100
    out.index.name = "symbol"
    return out.reset_index()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def fmt_table(df, columns, n):
    """Markdown table of the first n rows, with readable number formatting."""
    view = df[columns].head(n).copy()
    for col in view.columns:
        if col.startswith("short_20") or col in ("change_shares", "avg_daily_volume"):
            view[col] = view[col].map(lambda v: f"{v:,.0f}")
        elif view[col].dtype.kind == "f":
            view[col] = view[col].map(lambda v: f"{v:,.1f}")
    rows = [list(view.columns), ["---"] * len(view.columns), *view.astype(str).values.tolist()]
    return "\n".join("| " + " | ".join(row) + " |" for row in rows)


def build_report(si_table, sv_table, dates, sv_range, top):
    date_cols = [f"short_{d.isoformat()}" for d in dates]
    lines = [
        "# FINRA short interest report",
        "",
        f"Short interest settlement dates: {', '.join(d.isoformat() for d in dates)}",
    ]
    if sv_range:
        lines.append(f"Daily short volume window: {sv_range[0]} to {sv_range[1]}")
    lines.append("")

    if not si_table.empty:
        by_pct = si_table.sort_values("change_pct", ascending=False)
        by_shares = si_table.sort_values("change_shares", ascending=False)
        steady = by_pct[by_pct["rising_every_report"]]
        si_cols = ["symbol", "name", *date_cols, "change_shares", "change_pct", "days_to_cover"]
        lines += [
            f"## Biggest % increase in short interest (top {top})", "",
            fmt_table(by_pct, si_cols, top), "",
            f"## Biggest increase in shares short (top {top})", "",
            fmt_table(by_shares, si_cols, top), "",
            f"## Short interest up at every report (top {top} by % increase)", "",
            fmt_table(steady, si_cols, top), "",
        ]

    if not sv_table.empty:
        by_ratio = sv_table.sort_values("short_ratio_change_pts", ascending=False)
        sv_cols = ["symbol", "short_ratio_early", "short_ratio_late",
                   "short_ratio_change_pts", "avg_daily_volume"]
        lines += [
            f"## Biggest rise in daily short-volume ratio (top {top})", "",
            "Ratios are % of traded shares that were sold short (early vs late half of the window).", "",
            fmt_table(by_ratio, sv_cols, top), "",
        ]

    if not si_table.empty and not sv_table.empty:
        both = si_table.merge(sv_table[["symbol", "short_ratio_change_pts"]], on="symbol")
        both = both[(both["change_pct"] > 0) & (both["short_ratio_change_pts"] > 0)]
        both = both.sort_values("change_pct", ascending=False)
        lines += [
            f"## Rising on both signals (top {top})", "",
            "Short interest up over the window AND short-volume ratio up.", "",
            fmt_table(both, ["symbol", "name", "change_pct", "short_ratio_change_pts",
                             "days_to_cover"], top), "",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today(),
                        help="end of the window, YYYY-MM-DD (default: today)")
    parser.add_argument("--reports", type=int, default=3,
                        help="number of short interest reports to compare; 3 covers about one month (default: 3)")
    parser.add_argument("--volume-days", type=int, default=30,
                        help="calendar days of daily short volume to load (default: 30, 0 to skip)")
    parser.add_argument("--min-short-shares", type=float, default=1_000_000,
                        help="ignore stocks with fewer shares short at the latest report (default: 1,000,000)")
    parser.add_argument("--min-avg-volume", type=float, default=250_000,
                        help="ignore stocks trading fewer shares per day (default: 250,000)")
    parser.add_argument("--exclude-markets", nargs="*", default=["OTC"],
                        help="marketClassCode values to drop (default: OTC). Pass with no values to keep all.")
    parser.add_argument("--top", type=int, default=25, help="rows per table (default: 25)")
    parser.add_argument("--out", type=Path, default=HERE / "output", help="output folder")
    args = parser.parse_args()

    cache_dir = HERE / "data"
    cache_dir.mkdir(exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)

    session = make_session()
    dates = sorted(recent_settlement_dates(session, args.reports, args.as_of))
    if len(dates) < 2:
        raise SystemExit("Could not find at least two short interest reports from FINRA.")
    print("Short interest settlement dates:", ", ".join(d.isoformat() for d in dates))

    raw = pd.concat([fetch_short_interest(session, d, cache_dir) for d in dates], ignore_index=True)
    si = tidy_short_interest(raw)
    print("Rows per market at latest report:")
    print(si[si["date"] == dates[-1]]["market"].value_counts().to_string(), "\n")

    si_table = analyze_short_interest(si, args.min_short_shares, args.min_avg_volume, args.exclude_markets)
    si_table.sort_values("change_pct", ascending=False).to_csv(args.out / "short_interest_change.csv", index=False)

    sv_table, sv_range = pd.DataFrame(), None
    if args.volume_days > 0:
        sv = fetch_short_volume(args.as_of - timedelta(days=args.volume_days), args.as_of, cache_dir)
        if not sv.empty:
            sv_range = (sv["Date"].min().date(), sv["Date"].max().date())
            sv_table = analyze_short_volume(sv, args.min_avg_volume)
            sv_table.sort_values("short_ratio_change_pts", ascending=False).to_csv(
                args.out / "short_volume_trend.csv", index=False)

    report = build_report(si_table, sv_table, dates, sv_range, args.top)
    (args.out / "report.md").write_text(report)
    print(report)
    print(f"\nWrote {args.out / 'report.md'} and CSVs in {args.out}")


if __name__ == "__main__":
    main()
