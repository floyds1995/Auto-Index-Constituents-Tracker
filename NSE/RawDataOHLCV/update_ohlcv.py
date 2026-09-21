#!/usr/bin/env python3
"""
NSE Equity OHLCV Daily Updater

- Downloads NSE EOD bhavcopy (equity)
- Validates dates, rejects stale files (NSE serves previous day's data on holidays)
- Auto-creates csv/YYYY-MM/ folders for current month
- Auto-consolidates completed months → parquet/YYYY-MM.parquet (zstd, categorical, float32)
- Rebuilds manifest.csv every run (source of truth = filesystem)
- Safe to run repeatedly — idempotent, crash-safe

CLI:
    python update_ohlcv.py                    # default (up to yesterday)
    python update_ohlcv.py --dry-run          # show plan, no downloads
    python update_ohlcv.py --date 2026-09-18  # single specific date
    python update_ohlcv.py --no-consolidate   # skip Parquet conversion
"""
import argparse
import logging
import os
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from io import BytesIO, StringIO

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

# ============================================================
# Configuration
# ============================================================
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PARQUET_DIR = os.path.join(SCRIPT_DIR, "parquet")
CSV_DIR     = os.path.join(SCRIPT_DIR, "csv")
MANIFEST    = os.path.join(SCRIPT_DIR, "manifest.csv")
FAILED_CSV  = os.path.join(SCRIPT_DIR, "failed_events.csv")
SUMMARY_TXT = os.path.join(SCRIPT_DIR, "failed_events_summary.txt")

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

FIXED_HOLIDAYS = {
    "01-26": "Republic Day",
    "05-01": "Maharashtra Day",
    "08-15": "Independence Day",
    "10-02": "Gandhi Jayanti",
    "12-25": "Christmas",
}

PERMANENT_CLASSES = {"FIXED_HOLIDAY", "PERMANENT_GAP", "HOLIDAY_NON_FIXED"}
TRANSIENT_CLASSES = {"NETWORK_ERROR", "RATE_LIMITED", "PARSE_ERROR", "UNKNOWN"}

NORMALIZED_COLS = [
    "SYMBOL", "SERIES", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "LAST",
    "PREV_CLOSE", "VOLUME", "TURNOVER", "TRADES", "ISIN",
    "DELIV_QTY", "DELIV_PER", "SOURCE",
]

# Parquet output schema — matches the historical files
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
# Logging
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ohlcv")

# ============================================================
# URL builders
# ============================================================
def url_new(y, m, d):
    return (f"https://nsearchives.nseindia.com/products/content/"
            f"sec_bhavdata_full_{d:02d}{m:02d}{y}.csv")

def url_old(y, m, d):
    mn = date(y, m, d).strftime("%b").upper()
    return (f"https://nsearchives.nseindia.com/content/historical/EQUITIES/"
            f"{y}/{mn}/cm{d:02d}{mn}{y}bhav.csv.zip")

# ============================================================
# File paths
# ============================================================
def csv_path_for(d):
    folder = os.path.join(CSV_DIR, d.strftime("%Y-%m"))
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, d.strftime("%Y%m%d") + ".csv")

def parquet_path_for(ym):
    return os.path.join(PARQUET_DIR, f"{ym[:4]}-{ym[4:6]}.parquet")

# ============================================================
# HTTP with retries
# ============================================================
def http_get(url):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if r.status_code == 429:
                time.sleep(RETRY_BACKOFF ** attempt * 2)
                continue
            return r.status_code, r.content
        except requests.RequestException:
            if attempt == MAX_RETRIES:
                return "EXC", None
            time.sleep(RETRY_BACKOFF ** attempt)
    return "EXC", None

# ============================================================
# Validation
# ============================================================
def strip_df(df):
    df.columns = [c.strip() for c in df.columns]
    for col in df.columns:
        if df[col].dtype == "object":
            df[col] = df[col].astype(str).str.strip()
    return df

def validate_parsed(parsed_dates, expected_date, min_match=0.80):
    if parsed_dates is None or parsed_dates.isna().all():
        return False, "empty_date_column"
    ratio = (parsed_dates == expected_date).mean()
    if ratio < min_match:
        actual = parsed_dates.dropna().mode()
        actual_val = actual.iloc[0] if len(actual) else "unknown"
        return False, f"stale_file (contains {actual_val}, {ratio:.0%})"
    return True, "ok"

def validate_dataframe(df):
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
# Fetchers
# ============================================================
def fetch_new(y, m, d):
    url = url_new(y, m, d)
    status, content = http_get(url)
    if status != 200 or not content:
        return None, f"http_{status}" if status != 200 else "empty_response"
    if content[:15].lstrip().startswith(b"<"):
        return None, "html_error"
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
    y, m, dd = d.year, d.month, d.day
    reasons = []

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
                            "class": "PARSE_ERROR", "reason": "readback_empty"}
                return {"date": d.isoformat(), "status": "ok",
                        "source": "new", "rows": len(df)}
            reasons.append(f"new_validation: {vwhy}")
        else:
            reasons.append(f"new: {why}")
    else:
        reasons.append("new: skipped_pre2016")

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
                        "class": "PARSE_ERROR", "reason": "readback_empty"}
            return {"date": d.isoformat(), "status": "ok",
                    "source": "old", "rows": len(df)}
        reasons.append(f"old_validation: {vwhy}")
    else:
        reasons.append(f"old: {why}")

    mm_dd = d.strftime("%m-%d")
    if mm_dd in FIXED_HOLIDAYS:
        return {"date": d.isoformat(), "status": "fail",
                "class": "FIXED_HOLIDAY", "reason": FIXED_HOLIDAYS[mm_dd]}
    if any("stale_file" in r for r in reasons):
        return {"date": d.isoformat(), "status": "fail",
                "class": "HOLIDAY_NON_FIXED",
                "reason": "Market closed (NSE serves stale file)"}
    if any("http_404" in r for r in reasons):
        return {"date": d.isoformat(), "status": "fail",
                "class": "HOLIDAY_NON_FIXED",
                "reason": "Market closed (404 on all URLs)"}
    if any("empty_response" in r for r in reasons):
        return {"date": d.isoformat(), "status": "fail",
                "class": "PERMANENT_GAP", "reason": "NSE returns empty"}
    return {"date": d.isoformat(), "status": "fail",
            "class": "NETWORK_ERROR", "reason": " | ".join(reasons)[:300]}

# ============================================================
# Consolidate completed months → zstd Parquet
# ============================================================
def consolidate_completed_months(today):
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
        if os.path.exists(parquet_path):
            # Already consolidated — clean up stray CSVs
            for f in os.listdir(full):
                os.remove(os.path.join(full, f))
            try:
                os.rmdir(full)
            except OSError:
                pass
            continue

        csvs = sorted(f for f in os.listdir(full) if f.endswith(".csv"))
        if not csvs:
            continue

        log.info(f"Consolidating {folder} ({len(csvs)} files)...")
        frames = []
        for f in csvs:
            frames.append(pd.read_csv(os.path.join(full, f)))
        combined = pd.concat(frames, ignore_index=True)
        del frames

        # Row-count safety
        expected = sum(sum(1 for _ in open(os.path.join(full, f), "rb")) - 1
                       for f in csvs)
        if len(combined) < expected * 0.95:
            log.error(f"  only {len(combined):,} of {expected:,} rows — skipping")
            del combined
            continue

        # Cast to final schema
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

        os.makedirs(PARQUET_DIR, exist_ok=True)
        table = pa.Table.from_pandas(combined, schema=PARQUET_SCHEMA, preserve_index=False)
        pq.write_table(table, parquet_path, compression="zstd", compression_level=9)

        # Verify
        back = pd.read_parquet(parquet_path, columns=["SYMBOL"])
        if len(back) != len(combined):
            log.error(f"  readback mismatch: {len(back)} vs {len(combined)}")
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

        log.info(f"  ✅ {folder} → {os.path.basename(parquet_path)} ({len(combined):,} rows)")
        consolidated.append(folder)
        del combined, back

    return consolidated

# ============================================================
# Rebuild manifest from filesystem (source of truth)
# ============================================================
def rebuild_manifest():
    log.info("Rebuilding manifest from disk...")
    rows = []

    # Parquet
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
                        "date": grp["DATE"].isoformat(),
                        "location": f"parquet/{f}",
                        "rows": int(grp["rows"]),
                        "size_bytes": size,
                        "source": grp["SOURCE"],
                        "collected_at": "consolidated",
                    })
            except Exception as e:
                log.warning(f"  {f}: {e}")

    # CSV
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
                        "date": d.isoformat() if d else f"{f[:4]}-{f[4:6]}-{f[6:8]}",
                        "location": f"csv/{sub}/{f}",
                        "rows": len(df),
                        "size_bytes": os.path.getsize(path),
                        "source": src,
                        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })
                except Exception as e:
                    log.warning(f"  {f}: {e}")

    manifest = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    manifest.to_csv(MANIFEST, index=False)
    log.info(f"Manifest rebuilt: {len(manifest):,} rows")
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
        lines.append("Breakdown by class:")
        for cls, n in failed_df["class"].value_counts().items():
            lines.append(f"  {cls:22s} {int(n):4d}")
        lines.append("")
    lines.append("Coverage by year:")
    df_exp = pd.DataFrame({"date": [d.strftime("%Y%m%d") for d in expected_dates]})
    df_exp["year"] = df_exp["date"].str[:4]
    cov = df_exp.groupby("year").size().rename("expected").to_frame()
    df_have = pd.DataFrame({"date": sorted(have_dates)})
    if len(df_have):
        df_have["year"] = df_have["date"].str[:4]
        cov["on_disk"] = df_have.groupby("year").size().reindex(cov.index).fillna(0).astype(int)
    else:
        cov["on_disk"] = 0
    cov["expected"] = cov["expected"].astype(int)
    cov["pct"] = (cov["on_disk"] / cov["expected"] * 100).round(1)
    for yr, row in cov.iterrows():
        lines.append(f"  {yr}: {int(row['on_disk']):4d} / {int(row['expected']):4d}  ({row['pct']}%)")

    with open(SUMMARY_TXT, "w") as f:
        f.write("\n".join(lines))

# ============================================================
# Date helpers
# ============================================================
def expected_weekdays(start, end):
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
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-consolidate", action="store_true")
    ap.add_argument("--date", help="Single date YYYY-MM-DD")
    args = ap.parse_args()

    today = date.today()
    target_end = (date.fromisoformat(args.date) if args.date
                  else today - timedelta(days=1))

    log.info(f"Target end: {target_end}")

    # Load existing state
    manifest_df = pd.read_csv(MANIFEST) if os.path.exists(MANIFEST) else pd.DataFrame()
    manifest_dates = set(manifest_df["date"].tolist()) if len(manifest_df) else set()
    failed_df = pd.read_csv(FAILED_CSV) if os.path.exists(FAILED_CSV) else pd.DataFrame()

    permanent = set(failed_df[failed_df["class"].isin(PERMANENT_CLASSES)]["date"].tolist()) \
                if len(failed_df) else set()

    expected = expected_weekdays(COLLECTION_START, target_end)
    todo = [d for d in expected
            if d.isoformat() not in manifest_dates and d.isoformat() not in permanent]

    log.info(f"Expected weekdays: {len(expected)}")
    log.info(f"Already on disk:   {len(manifest_dates)}")
    log.info(f"Permanent fails:   {len(permanent)}")
    log.info(f"To fetch:          {len(todo)}")

    if args.dry_run:
        for d in todo[:30]:
            log.info(f"  would fetch {d}")
        if len(todo) > 30:
            log.info(f"  ... and {len(todo) - 30} more")
        return

    # Download
    new_failures = []
    for i, d in enumerate(todo, 1):
        log.info(f"[{i}/{len(todo)}] {d} ({d.strftime('%A')})")
        result = download_date(d)
        if result["status"] == "ok":
            log.info(f"  ✅ {result['source']} ({result['rows']} rows)")
        else:
            new_failures.append({
                "date": result["date"],
                "weekday": d.strftime("%A"),
                "class": result["class"],
                "reason": result["reason"],
            })
            log.warning(f"  ❌ {result['class']}: {result['reason']}")

    if new_failures:
        combined_fail = pd.concat([failed_df, pd.DataFrame(new_failures)], ignore_index=True)
        combined_fail = combined_fail.drop_duplicates(subset=["date"], keep="last")
        combined_fail.to_csv(FAILED_CSV, index=False)
        failed_df = combined_fail
        log.info(f"Failures updated: +{len(new_failures)}")

    # Consolidate
    if not args.no_consolidate:
        consolidated = consolidate_completed_months(today)
        if consolidated:
            log.info(f"Consolidated {len(consolidated)} month(s)")

    # Rebuild manifest from disk (source of truth)
    manifest_df = rebuild_manifest()

    # Write summary
    write_summary(expected, manifest_df, failed_df)
    log.info("Done.")

if __name__ == "__main__":
    main()
