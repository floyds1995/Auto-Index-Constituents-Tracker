#!/usr/bin/env python3
"""
Sync NSE corporate actions into NSE/RawData/CorporateActions.csv.

Design:
  - Fetches the last 7 days of corporate actions from NSE's API.
  - Compares semantically (whitespace, case, date format, blank-vs-dash).
  - Existing local rows are NEVER modified or removed.
  - Only genuinely new rows are appended.
  - Preserves EX-DATE-ascending order (append at bottom).
"""

from __future__ import annotations

import io
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

# ── Config ────────────────────────────────────────────────────────────
API   = "https://www.nseindia.com/api/corporates-corporateActions"
HOME  = "https://www.nseindia.com"
WINDOW_DAYS = 7

SCRIPT_DIR  = Path(__file__).resolve().parent
LOCAL_FILE  = SCRIPT_DIR / "CorporateActions.csv"
BACKUP_FILE = SCRIPT_DIR / "CorporateActions.csv.bak"

COLS = [
    "SYMBOL", "COMPANY NAME", "SERIES", "PURPOSE", "FACE VALUE",
    "EX-DATE", "RECORD DATE", "BOOK CLOSURE START DATE", "BOOK CLOSURE END DATE",
]
DATE_COLS = {"EX-DATE", "RECORD DATE", "BOOK CLOSURE START DATE", "BOOK CLOSURE END DATE"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-actions",
    "X-Requested-With": "XMLHttpRequest",
}


def _t_series(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\s+", " ", regex=True).str.strip().str.lower()


def _d_series(s: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(s, dayfirst=True, errors="coerce", format="mixed")
    return parsed.dt.strftime("%Y-%m-%d").fillna(_t_series(s))


def row_key_series(df: pd.DataFrame) -> pd.Series:
    parts = [
        _d_series(df[c]) if c in DATE_COLS else _t_series(df[c])
        for c in COLS
    ]
    k = parts[0]
    for p in parts[1:]:
        k = k + "|" + p
    return k


def fetch_remote(days: int = WINDOW_DAYS) -> pd.DataFrame:
    session = requests.Session()
    session.get(HOME, headers=HEADERS, timeout=15)

    today = datetime.now()
    params = {
        "index":     "equities",
        "from_date": (today - timedelta(days=days - 1)).strftime("%d-%m-%Y"),
        "to_date":   today.strftime("%d-%m-%Y"),
        "csv":       "true",
    }
    r = session.get(API, headers=HEADERS, params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for {API}")

    df = pd.read_csv(
        io.StringIO(r.content.decode("utf-8", errors="replace")),
        dtype=str,
        keep_default_na=False,
    )
    df.columns = [c.strip() for c in df.columns]

    missing = [c for c in COLS if c not in df.columns]
    if missing:
        raise RuntimeError(f"Remote response missing columns: {missing}")

    print(f"✅ Remote OK: {len(df)} rows for window {params['from_date']} → {params['to_date']}")
    return df


def main() -> int:
    if not LOCAL_FILE.exists():
        print(f"❌ {LOCAL_FILE} not found. Commit an initial copy first.")
        return 1

    df_local = pd.read_csv(LOCAL_FILE, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df_local.columns = [c.strip() for c in df_local.columns]
    print(f"📂 Local: {len(df_local)} rows (read as-is, never modified)")

    local_keys = set(row_key_series(df_local))

    try:
        df_remote = fetch_remote()
    except Exception as exc:
        print(f"❌ Fetch failed: {exc}")
        return 1

    remote_keys = row_key_series(df_remote)
    mask_new    = ~remote_keys.isin(local_keys)
    df_new      = df_remote.loc[mask_new].copy()

    print(f"➕ new rows: {len(df_new)}")

    if len(df_new) == 0:
        print("✅ Nothing new. Local file unchanged.")
        return 0

    for c in COLS:
        if c not in df_new.columns:
            df_new[c] = ""

    merged = pd.concat([df_local[COLS], df_new[COLS]], ignore_index=True)

    BACKUP_FILE.write_bytes(LOCAL_FILE.read_bytes())
    merged.to_csv(LOCAL_FILE, index=False)

    print(f"✅ Wrote {LOCAL_FILE.name}: {len(df_local)} → {len(merged)} (+{len(df_new)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())