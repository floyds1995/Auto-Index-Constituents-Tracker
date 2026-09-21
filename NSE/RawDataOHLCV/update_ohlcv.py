#!/usr/bin/env python3
"""
NSE Equity OHLCV Daily Updater
==============================

Downloads daily EOD bhavcopy for NSE equity, validates freshness,
auto-consolidates completed months into Parquet, and keeps a manifest.

Run every day via GitHub Actions. Safe to run repeatedly — idempotent.

CLI:
    python update_ohlcv.py                    # default (up to yesterday)
    python update_ohlcv.py --dry-run          # show plan, no downloads
    python update_ohlcv.py --date 2026-09-18  # single specific date
    python update_ohlcv.py --no-consolidate   # skip Parquet conversion

Exit codes:
    0  success
    1  unexpected error
"""

import argparse
import logging
import os
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from io import BytesIO, StringIO

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

# ============================================================
# Paths and constants
# ============================================================
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PARQUET_DIR  = os.path.join(SCRIPT_DIR, "parquet")
CSV_DIR      = os.path.join(SCRIPT_DIR, "csv")
MANIFEST     = os.path.join(SCRIPT_DIR, "manifest.csv")
FAILED_CSV   = os.path.join(SCRIPT_DIR, "failed_events.csv")
SUMMARY_TXT  = os.path.join(SCRIPT_DIR, "failed_events_summary.txt")

COLLECTION_START = date(1995, 1, 2)
REQUEST_TIMEOUT  = 30
REQUEST_DELAY    = 0.3
MAX_RETRIES      = 3
RETRY_BACKOFF    = 2.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/csv,application/csv,*/*",
}

# NSE fixed market holidays — never retried
FIXED_HOLIDAYS = {
    "01-26": "Republic Day",
    "05-01": "Maharashtra Day",
    "08-15": "Independence Day",
    "10-02": "Gandhi Jayanti",
    "12-25": "Christmas",
}

# Failure categories — never retry these (they're permanent)
PERMANENT_CATEGORIES = {"FIXED_HOLIDAY", "PERMANENT_GAP", "HOLIDAY_NON_FIXED"}

# Normalized output schema for all files
NORMALIZED_COLS = [
    "SYMBOL", "SERIES", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "LAST",
    "PREV_CLOSE", "VOLUME", "TURNOVER", "TRADES", "ISIN",
    "DELIV_QTY", "DELIV_PER", "SOURCE",
]

# Parquet output schema — matches historical files
PARQUET_SCHEMA = pa.schema([
    ("SYMBOL",     pa.dictionary(pa.int32(), pa.string())),
    ("SERIES",     pa.dictionary(pa.int32(), pa.string())),
    ("DATE",       pa.timestamp("ns")),
    ("OPEN",       pa.float32()),
    ("HIGH",       pa.float32()),
    ("LOW",        pa.float32()),
    ("CLOSE",      pa.float32()),
    ("LAST",       pa.float32()),
    ("PREV_CLOSE", pa.float32()),
    ("VOLUME",     pa.int64()),
    ("TURNOVER",   pa.float64()),
    ("TRADES",     pa.int64()),
    ("ISIN",       pa.string()),
    ("DELIV_QTY",  pa.int64()),
    ("DELIV_PER",  pa.float32()),
    ("SOURCE",     pa.dictionary(pa.int32(), pa.string())),
])

# ============================================================
# Logging — clean and simple for GitHub Actions output
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",   # don't clutter with timestamps/levels
)
log = logging.getLogger("ohlcv")

def section(title):
    """Print a section header."""
    log.info("")
    log.info("=" * 60)
    log.info(title)
    log.info("=" * 60)

def info(msg):  log.info(msg)
def warn(msg):  log.warning(f"⚠️  {msg}")
def good(msg):  log.info(f"✅ {msg}")
def bad(msg):   log.warning(f"❌ {msg}")

# ============================================================
# URL builders
# ============================================================
def url_new(y, m, d):
    """Current NSE URL (2019+, includes delivery data)."""
    return (f"https://nsearchives.nseindia.com/products/content/"
            f"sec_bhavdata_full_{d:02d}{m:02d}{y}.csv")

def url_old(y, m, d):
    """Legacy NSE URL (1995–2019, zipped bhavcopy)."""
    mn = date(y, m, d).strftime("%b").upper()
    return (f"https://nsearchives.nseindia.com/content/historical/EQUITIES/"
            f"{y}/{mn}/cm{d:02d}{mn}{y}bhav.csv.zip")

# ============================================================
# Filesystem paths
# ============================================================
def csv_path_for(d):
    """csv/YYYY-MM/YYYYMMDD.csv — creates month folder if needed."""
    folder = os.path.join(CSV_DIR, d.strftime("%Y-%m"))
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, d.strftime("%Y%m%d") + ".csv")

def parquet_path_for(ym):
    """parquet/YYYY-MM.parquet"""
    return os.path.join(PARQUET_DIR, f"{ym[:4]}-{ym[4:6]}.parquet")

# ============================================================
# HTTP with retries
# ============================================================
def http_get(url):
    """GET with retries. Returns (status_code, content)."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if r.status_code == 429:
                time.sleep(RETRY_BACKOFF ** attempt * 2)
                continue
            return r.status_code, r.content
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                return "EXC", None
            time.sleep(RETRY_BACKOFF ** attempt)
    return "EXC", None

# ============================================================
# Validation helpers
# ============================================================
def strip_df(df):
    """Strip whitespace from column names AND all string values."""
    df.columns = [c.strip() for c in df.columns]
    for col in df.columns:
        if df[col].dtype == "object":
            df[col] = df[col].astype(str).str.strip()
    return df

def validate_parsed(parsed_dates, expected_date, min_match=0.80):
    """
    Reject stale files. NSE serves previous day's data on holiday URLs.
    Requires >= 80% of rows to match the expected date.
    """
    if parsed_dates is None or parsed_dates.isna().all():
        return False, "empty_date_column"
    ratio = (parsed_dates == expected_date).mean()
    if ratio < min_match:
        actual = parsed_dates.dropna().mode()
        actual_val = actual.iloc[0] if len(actual) else "unknown"
        return False, f"stale_file (contains {actual_val}, {ratio:.0%} match)"
    return True, "ok"

def validate_dataframe(df):
    """Sanity check on the normalized DataFrame before saving."""
    if df is None or len(df) == 0:
        return False, "empty_dataframe"
    if df["SYMBOL"].nunique() < 100:
        return False, f"too_few_symbols ({df['SYMBOL'].nunique()})"
    for col in ["OPEN", "HIGH", "LOW", "CLOSE"]:
        if df[col].notna().mean() < 0.90:
            return False, f"null_{col}"
    valid = (df["HIGH"] >= df["LOW"]) & df["HIGH"].notna() & df["LOW"].notna()
    if valid.mean() < 0.90:
        return False, "ohlc_sanity_fail"
    return True, "ok"

# ============================================================
# Fetchers — return (DataFrame, reason)
# ============================================================
def fetch_new(y, m, d):
    """Try the NEW (2019+) URL."""
    url = url_new(y, m, d)
    status, content = http_get(url)
    if status != 200 or not content:
        return None, f"http_{status}" if status != 200 else "empty_response"
    if content[:15].lstrip().startswith(b"<"):
        return None, "html_error_page"
    try:
        df = pd.read_csv(StringIO(content.decode("utf-8", errors="replace")))
        df = strip_df(df)
        if "DATE1" not in df.columns:
            return None, "missing_DATE1"
        parsed = pd.to_datetime(df["DATE1"], format="%d-%b-%Y", errors="coerce").dt.date
        ok, why = validate_parsed(parsed, date(y, m, d))
        if not ok:
            return None, why
        out = pd.DataFrame({
            "SYMBOL":     df["SYMBOL"],
            "SERIES":     df["SERIES"],
            "DATE":       parsed,
            "OPEN":       pd.to_numeric(df["OPEN_PRICE"], errors="coerce"),
            "HIGH":       pd.to_numeric(df["HIGH_PRICE"], errors="coerce"),
            "LOW":        pd.to_numeric(df["LOW_PRICE"], errors="coerce"),
            "CLOSE":      pd.to_numeric(df["CLOSE_PRICE"], errors="coerce"),
            "LAST":       pd.to_numeric(df["LAST_PRICE"], errors="coerce"),
            "PREV_CLOSE": pd.to_numeric(df["PREV_CLOSE"], errors="coerce"),
            "VOLUME":     pd.to_numeric(df["TTL_TRD_QNTY"], errors="coerce"),
            "TURNOVER":   pd.to_numeric(df["TURNOVER_LACS"], errors="coerce") * 100_000,
            "TRADES":     pd.to_numeric(df["NO_OF_TRADES"], errors="coerce"),
            "ISIN":       None,
            "DELIV_QTY":  pd.to_numeric(df["DELIV_QTY"], errors="coerce"),
            "DELIV_PER":  pd.to_numeric(df["DELIV_PER"], errors="coerce"),
            "SOURCE":     "new",
        })
        return out[NORMALIZED_COLS], "ok"
    except Exception as e:
        return None, f"parse_error: {type(e).__name__}"

def fetch_old(y, m, d):
    """Try the OLD (pre-2019) URL (zip archive)."""
    url = url_old(y, m, d)
    status, content = http_get(url)
    if status != 200 or not content:
        return None, f"http_{status}" if status != 200 else "empty_response"
    if content[:4] != b"PK\x03\x04":
        return None, "not_a_zip"
    try:
        with zipfile.ZipFile(BytesIO(content)) as z:
            with z.open(z.namelist()[0]) as f:
                df = pd.read_csv(f)
        df = strip_df(df)
        if "TIMESTAMP" not in df.columns:
            return None, "missing_TIMESTAMP"
        parsed = pd.to_datetime(df["TIMESTAMP"], format="%d-%b-%Y", errors="coerce").dt.date
        ok, why = validate_parsed(parsed, date(y, m, d))
        if not ok:
            return None, why
        isin = df["ISIN"] if "ISIN" in df.columns else None
        trades = (pd.to_numeric(df["TOTALTRADES"], errors="coerce")
                  if "TOTALTRADES" in df.columns else None)
        out = pd.DataFrame({
            "SYMBOL":     df["SYMBOL"],
            "SERIES":     df["SERIES"],
            "DATE":       parsed,
            "OPEN":       pd.to_numeric(df["OPEN"], errors="coerce"),
            "HIGH":       pd.to_numeric(df["HIGH"], errors="coerce"),
            "LOW":        pd.to_numeric(df["LOW"], errors="coerce"),
            "CLOSE":      pd.to_numeric(df["CLOSE"], errors="coerce"),
            "LAST":       pd.to_numeric(df["LAST"], errors="coerce"),
            "PREV_CLOSE": pd.to_numeric(df["PREVCLOSE"], errors="coerce"),
            "VOLUME":     pd.to_numeric(df["TOTTRDQTY"], errors="coerce"),
            "TURNOVER":   pd.to_numeric(df["TOTTRDVAL"], errors="coerce"),
            "TRADES":     trades,
            "ISIN":       isin,
            "DELIV_QTY":  None,
            "DELIV_PER":  None,
            "SOURCE":     "old",
        })
        return out[NORMALIZED_COLS], "ok"
    except zipfile.BadZipFile:
        return None, "bad_zip"
    except Exception as e:
        return None, f"parse_error: {type(e).__name__}"

# ============================================================
# Download one date
# ============================================================
def download_date(d):
    """
    Download a single date.
    Returns dict with: date, status (ok/fail), source, rows,
    category (if fail), reason (if fail).
    """
    y, m, dd = d.year, d.month, d.day
    reasons = []

    # Try NEW first for 2016+
    if y >= 2016:
        df, why = fetch_new(y, m, dd)
        time.sleep(REQUEST_DELAY)
        if df is not None:
            ok, vwhy = validate_dataframe(df)
            if ok:
                path = csv_path_for(d)
                df.to_csv(path, index=False)
                back = pd.read_csv(path, nrows=5)
                if len(back) == 0:
                    os.remove(path)
                    return {"date": d.isoformat(), "status": "fail",
                            "category": "PARSE_ERROR", "reason": "readback_empty"}
                return {"date": d.isoformat(), "status": "ok",
                        "source": "new", "rows": len(df)}
            reasons.append(f"new_validation: {vwhy}")
        else:
            reasons.append(f"new: {why}")
    else:
        reasons.append("new: skipped_pre2016")

    # Try OLD as fallback
    df, why = fetch_old(y, m, dd)
    time.sleep(REQUEST_DELAY)
    if df is not None:
        ok, vwhy = validate_dataframe(df)
        if ok:
            path = csv_path_for(d)
            df.to_csv(path, index=False)
            back = pd.read_csv(path, nrows=5)
            if len(back) == 0:
                os.remove(path)
                return {"date": d.isoformat(), "status": "fail",
                        "category": "PARSE_ERROR", "reason": "readback_empty"}
            return {"date": d.isoformat(), "status": "ok",
                    "source": "old", "rows": len(df)}
        reasons.append(f"old_validation: {vwhy}")
    else:
        reasons.append(f"old: {why}")

    # ---- Classify the failure ----
    mm_dd = d.strftime("%m-%d")
    if mm_dd in FIXED_HOLIDAYS:
        return {"date": d.isoformat(), "status": "fail",
                "category": "FIXED_HOLIDAY", "reason": FIXED_HOLIDAYS[mm_dd]}
    if any("stale_file" in r for r in reasons):
        return {"date": d.isoformat(), "status": "fail",
                "category": "HOLIDAY_NON_FIXED",
                "reason": "Market closed (NSE serves stale file)"}
    if any("http_404" in r for r in reasons):
        return {"date": d.isoformat(), "status": "fail",
                "category": "HOLIDAY_NON_FIXED",
                "reason": "Market closed (404 on all URLs)"}
    if any("empty_response" in r for r in reasons):
        return {"date": d.isoformat(), "status": "fail",
                "category": "PERMANENT_GAP", "reason": "NSE returns empty"}
    return {"date": d.isoformat(), "status": "fail",
            "category": "NETWORK_ERROR", "reason": " | ".join(reasons)[:300]}

# ============================================================
# Consolidate completed months → Parquet
# ============================================================
def consolidate_completed_months(today):
    """
    For every csv/YYYY-MM folder where month < current month,
    write parquet/YYYY-MM.parquet and delete the CSV folder.
    """
    if not os.path.isdir(CSV_DIR):
        return []

    consolidated = []
    for folder in sorted(os.listdir(CSV_DIR)):
        full = os.path.join(CSV_DIR, folder)
        if not os.path.isdir(full) or len(folder) != 7:
            continue

        ym = folder.replace("-", "")
        if ym >= today.strftime("%Y%m"):
            continue  # current month or future

        parquet_path = parquet_path_for(ym)
        csvs = sorted(f for f in os.listdir(full) if f.endswith(".csv"))

        # Already consolidated — clean up stray CSVs
        if os.path.exists(parquet_path):
            for f in csvs:
                os.remove(os.path.join(full, f))
            try:
                os.rmdir(full)
            except OSError:
                pass
            continue

        if not csvs:
            continue

        info(f"  Consolidating {folder}: {len(csvs)} CSVs → 1 Parquet")

        # Read all CSVs
        frames = [pd.read_csv(os.path.join(full, f)) for f in csvs]
        combined = pd.concat(frames, ignore_index=True)
        del frames

        # Row-count safety check
        expected = sum(sum(1 for _ in open(os.path.join(full, f), "rb")) - 1
                       for f in csvs)
        if len(combined) < expected * 0.95:
            bad(f"  Skipping {folder} — only {len(combined):,} of {expected:,} rows")
            del combined
            continue

        # Cast types to match historical Parquet
        combined["DATE"] = pd.to_datetime(combined["DATE"], errors="coerce")
        for col in ["SYMBOL", "SERIES", "SOURCE"]:
            if col in combined.columns:
                combined[col] = combined[col].astype(str)
        if "ISIN" in combined.columns:
            combined["ISIN"] = combined["ISIN"].astype(str).replace("nan", None)
        for col in ["OPEN","HIGH","LOW","CLOSE","LAST","PREV_CLOSE","DELIV_PER"]:
            if col in combined.columns:
                combined[col] = pd.to_numeric(combined[col], errors="coerce").astype("float32")
        for col in ["VOLUME","TRADES","DELIV_QTY"]:
            if col in combined.columns:
                combined[col] = pd.to_numeric(combined[col], errors="coerce").astype("Int64")
        if "TURNOVER" in combined.columns:
            combined["TURNOVER"] = pd.to_numeric(combined["TURNOVER"], errors="coerce")

        combined = combined.sort_values(["SYMBOL", "DATE"]).reset_index(drop=True)
        combined = combined[[c for c in [f.name for f in PARQUET_SCHEMA]
                             if c in combined.columns]]

        # Write Parquet
        os.makedirs(PARQUET_DIR, exist_ok=True)
        table = pa.Table.from_pandas(combined, schema=PARQUET_SCHEMA, preserve_index=False)
        pq.write_table(table, parquet_path, compression="zstd", compression_level=9)

        # Verify by reading back
        back = pd.read_parquet(parquet_path, columns=["SYMBOL"])
        if len(back) != len(combined):
            bad(f"  Readback mismatch: {len(back)} vs {len(combined)} — keeping CSVs")
            os.remove(parquet_path)
            del combined, back
            continue

        # Delete CSVs
        for f in csvs:
            os.remove(os.path.join(full, f))
        try:
            os.rmdir(full)
        except OSError:
            pass

        size_mb = os.path.getsize(parquet_path) / 1024 / 1024
        good(f"  {folder} → {os.path.basename(parquet_path)} "
             f"({len(combined):,} rows, {size_mb:.1f} MB)")
        consolidated.append(folder)
        del combined, back

    return consolidated

# ============================================================
# Rebuild manifest from filesystem
# ============================================================
def rebuild_manifest():
    """Scan parquet/ and csv/ — regenerate manifest.csv from scratch."""
    rows = []

    # Parquet files
    if os.path.isdir(PARQUET_DIR):
        for f in sorted(os.listdir(PARQUET_DIR)):
            if not f.endswith(".parquet"):
                continue
            path = os.path.join(PARQUET_DIR, f)
            size = os.path.getsize(path)
            try:
                df = pd.read_parquet(path, columns=["DATE", "SOURCE"])
                df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
                grouped = (df.groupby([df["DATE"].dt.date, "SOURCE"], observed=True)
                             .size().reset_index(name="rows"))
                for _, grp in grouped.iterrows():
                    rows.append({
                        "date":         grp["DATE"].isoformat(),
                        "location":     f"parquet/{f}",
                        "rows":         int(grp["rows"]),
                        "size_bytes":   size,
                        "source":       grp["SOURCE"],
                        "collected_at": "consolidated",
                    })
            except Exception as e:
                warn(f"  Could not read {f}: {e}")

    # CSV files
    if os.path.isdir(CSV_DIR):
        for sub in sorted(os.listdir(CSV_DIR)):
            sub_path = os.path.join(CSV_DIR, sub)
            if not os.path.isdir(sub_path):
                continue
            for f in sorted(os.listdir(sub_path)):
                if not f.endswith(".csv"):
                    continue
                path = os.path.join(sub_path, f)
                try:
                    df = pd.read_csv(path, usecols=["DATE", "SOURCE"])
                    df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
                    d = df["DATE"].dropna().iloc[0].date() if len(df) else None
                    src = df["SOURCE"].iloc[0] if "SOURCE" in df.columns else "unknown"
                    rows.append({
                        "date":         d.isoformat() if d else f"{f[:4]}-{f[4:6]}-{f[6:8]}",
                        "location":     f"csv/{sub}/{f}",
                        "rows":         len(df),
                        "size_bytes":   os.path.getsize(path),
                        "source":       src,
                        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })
                except Exception as e:
                    warn(f"  Could not read {f}: {e}")

    manifest = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    manifest.to_csv(MANIFEST, index=False)
    return manifest

# ============================================================
# Summary writer
# ============================================================
def write_summary(expected_dates, manifest_df, failed_df):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    have_dates = set(manifest_df["date"].tolist()) if len(manifest_df) else set()

    lines = [
        "NSE Equity Data — Failed Events Summary",
        f"Generated: {now}",
        f"Range:     {COLLECTION_START} → {date.today() - timedelta(days=1)}",
        "",
        f"Expected weekdays:  {len(expected_dates)}",
        f"Successfully on disk: {len(have_dates)}",
        f"Missing (total):    {len(expected_dates) - len(have_dates)}",
        "",
    ]

    if len(failed_df):
        lines.append("Breakdown by category:")
        # Support both old and new column names
        col = "category" if "category" in failed_df.columns else "class"
        for cat, n in failed_df[col].value_counts().items():
            lines.append(f"  {cat:22s} {int(n):4d}")
        lines.append("")

    lines.append("Coverage by year:")
    df_exp = pd.DataFrame({"date": [d.strftime("%Y%m%d") for d in expected_dates]})
    df_exp["year"] = df_exp["date"].str[:4]
    cov = df_exp.groupby("year").size().rename("expected").to_frame()
    df_have = pd.DataFrame({"date": sorted(have_dates)})
    if len(df_have):
        df_have["year"] = df_have["date"].str[:4]
        cov["on_disk"] = (df_have.groupby("year").size()
                          .reindex(cov.index).fillna(0).astype(int))
    else:
        cov["on_disk"] = 0
    cov["expected"] = cov["expected"].astype(int)
    cov["pct"] = (cov["on_disk"] / cov["expected"] * 100).round(1)
    for yr, row in cov.iterrows():
        lines.append(f"  {yr}: {int(row['on_disk']):4d} / "
                     f"{int(row['expected']):4d}  ({row['pct']}%)")

    with open(SUMMARY_TXT, "w") as f:
        f.write("\n".join(lines))

# ============================================================
# Date helpers
# ============================================================
def expected_weekdays(start, end):
    """All Mon-Fri dates between start and end, inclusive."""
    out, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out

# ============================================================
# Main
# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be fetched, don't download")
    ap.add_argument("--no-consolidate", action="store_true",
                    help="Skip Parquet conversion this run")
    ap.add_argument("--date", help="Single date YYYY-MM-DD")
    args = ap.parse_args()

    today = date.today()
    target_end = (date.fromisoformat(args.date) if args.date
                  else today - timedelta(days=1))

    # =========================================================
    # STEP 1 — Load state
    # =========================================================
    section("STEP 1 — Load state")
    info(f"Target date range:  {COLLECTION_START} → {target_end}")

    manifest_df = (pd.read_csv(MANIFEST) if os.path.exists(MANIFEST)
                   else pd.DataFrame())
    manifest_dates = set(manifest_df["date"].tolist()) if len(manifest_df) else set()
    info(f"Manifest:           {len(manifest_dates):,} dates collected")

    failed_df = (pd.read_csv(FAILED_CSV) if os.path.exists(FAILED_CSV)
                 else pd.DataFrame())

    # Support both "category" (new) and "class" (old) column names
    if len(failed_df):
        fail_col = "category" if "category" in failed_df.columns else "class"
        permanent = set(
            failed_df[failed_df[fail_col].isin(PERMANENT_CATEGORIES)]["date"].tolist()
        )
        info(f"Failed events:      {len(failed_df):,} total, "
             f"{len(permanent):,} permanent (holidays/gaps)")
    else:
        permanent = set()
        info(f"Failed events:      (none)")

    # =========================================================
    # STEP 2 — Compute missing dates
    # =========================================================
    section("STEP 2 — Compute what's missing")

    expected = expected_weekdays(COLLECTION_START, target_end)
    info(f"Expected weekdays:  {len(expected):,}")

    todo = [d for d in expected
            if d.isoformat() not in manifest_dates
            and d.isoformat() not in permanent]
    info(f"To download:        {len(todo):,}")

    if not todo:
        info("")
        good("Nothing to fetch — collection is up to date.")
        # Still consolidate and rebuild manifest (cheap ops)
        if not args.no_consolidate:
            section("STEP 5 — Check for completed months")
            consolidated = consolidate_completed_months(today)
            if consolidated:
                good(f"Consolidated {len(consolidated)} month(s)")
            else:
                info("No completed months to consolidate.")
        section("STEP 7 — Rebuild manifest")
        manifest_df = rebuild_manifest()
        info(f"Manifest now:       {len(manifest_df):,} dates")
        write_summary(expected, manifest_df, failed_df)
        info("")
        good("Done.")
        return

    # Dry run — show plan and exit
    if args.dry_run:
        info("")
        info("Dry run — would fetch:")
        for d in todo[:30]:
            info(f"  {d}  ({d.strftime('%A')})")
        if len(todo) > 30:
            info(f"  ... and {len(todo) - 30} more")
        return

    # =========================================================
    # STEP 3 — Download
    # =========================================================
    section(f"STEP 3 — Downloading {len(todo)} date(s)")

    new_failures = []
    downloaded = 0
    for i, d in enumerate(todo, 1):
        info(f"[{i}/{len(todo)}] {d} ({d.strftime('%A')})")
        result = download_date(d)

        if result["status"] == "ok":
            downloaded += 1
            good(f"     {result['source']} format, {result['rows']:,} rows")
        else:
            new_failures.append({
                "date":     result["date"],
                "weekday":  d.strftime("%A"),
                "category": result["category"],
                "reason":   result["reason"],
            })
            bad(f"     {result['category']}: {result['reason']}")

    info("")
    good(f"Downloaded: {downloaded} / {len(todo)}")
    if new_failures:
        bad(f"Failed:     {len(new_failures)}")

    # =========================================================
    # STEP 4 — Save failures
    # =========================================================
    if new_failures:
        section("STEP 4 — Log new failures")
        combined_fail = pd.concat([failed_df, pd.DataFrame(new_failures)],
                                  ignore_index=True)
        # Normalize column name to "category"
        if "class" in combined_fail.columns and "category" not in combined_fail.columns:
            combined_fail = combined_fail.rename(columns={"class": "category"})
        combined_fail = combined_fail.drop_duplicates(subset=["date"], keep="last")
        combined_fail.to_csv(FAILED_CSV, index=False)
        failed_df = combined_fail
        info(f"failed_events.csv updated: {len(failed_df):,} total rows")

    # =========================================================
    # STEP 5 — Consolidate completed months
    # =========================================================
    if not args.no_consolidate:
        section("STEP 5 — Check for completed months")
        consolidated = consolidate_completed_months(today)
        if consolidated:
            good(f"Consolidated {len(consolidated)} month(s) into Parquet")
        else:
            info("No completed months to consolidate this run.")

    # =========================================================
    # STEP 6 — Rebuild manifest
    # =========================================================
    section("STEP 6 — Rebuild manifest from disk")
    manifest_df = rebuild_manifest()
    info(f"Manifest rows:  {len(manifest_df):,}")
    if len(manifest_df):
        info(f"Date range:     {manifest_df['date'].min()} → "
             f"{manifest_df['date'].max()}")

    # =========================================================
    # STEP 7 — Write summary
    # =========================================================
    section("STEP 7 — Write summary")
    write_summary(expected, manifest_df, failed_df)
    info(f"Wrote: {SUMMARY_TXT}")

    # =========================================================
    # DONE
    # =========================================================
    info("")
    section("DONE")
    good(f"{downloaded} new date(s) collected")
    if new_failures:
        info(f"{len(new_failures)} date(s) failed (see failed_events.csv)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error("")
        log.error("=" * 60)
        log.error(f"FATAL ERROR: {e}")
        log.error("=" * 60)
        import traceback
        traceback.print_exc()
        sys.exit(1)
