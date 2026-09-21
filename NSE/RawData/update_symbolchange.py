#!/usr/bin/env python3
"""
Sync NSE symbolchange.csv into NSE/RawData/symbolchange.csv.

Behaviour:
  - Fetches the remote CSV from NSE.
  - Validates the link structurally (no header assumed on remote).
  - Compares semantically against the local file (whitespace, case, date
    format, leading-zero numeric symbols all normalised).
  - Appends only genuinely new rows; never deletes; preserves local schema.
  - Writes a .bak before overwriting.

Exit codes:
  0  success (whether or not the file changed)
  1  error (network, validation, or schema failure)
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pandas as pd
import requests

# ── Configuration ──────────────────────────────────────────────────────
NSE_CSV_URL = "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"

SCRIPT_DIR  = Path(__file__).resolve().parent      # NSE/RawData
LOCAL_FILE  = SCRIPT_DIR / "symbolchange.csv"
BACKUP_FILE = SCRIPT_DIR / "symbolchange.csv.bak"

DATE_FORMAT = "%d-%b-%Y"   # canonical output: 30-Oct-2019
SORT_OUTPUT = True         # set False to preserve insertion order
CORE_COLS   = ["Name", "Old Symbol", "New Symbol", "Date of change"]
TIMEOUT     = 30


# ── Fetch + validate ───────────────────────────────────────────────────
def fetch_remote(url: str) -> bytes:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0 Safari/537.36"
        ),
        "Accept": "text/csv,application/csv,*/*",
        "Referer": "https://www.nseindia.com/",
    }
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for {url}")

    text  = r.content.decode("utf-8-sig", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise RuntimeError("Remote file is empty or truncated.")

    for i, ln in enumerate(lines[:20]):
        if len(ln.split(",")) != 4:
            raise RuntimeError(
                f"Structure validation failed at line {i}: "
                f"expected 4 columns, got {len(ln.split(','))}"
            )
    print(f"✅ Remote OK: {len(lines)} lines, 4 columns each.")
    return r.content


# ── Normalisation ──────────────────────────────────────────────────────
def _norm_text(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _norm_symbol(s) -> str:
    t = _norm_text(s).upper()
    if t.isdigit():
        stripped = t.lstrip("0")
        return stripped or "0"
    return t


def _norm_date(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    raw = str(s).strip()
    if not raw:
        return ""
    try:
        return pd.to_datetime(
            raw, dayfirst=True, errors="raise", format="mixed"
        ).strftime("%Y-%m-%d")
    except Exception:
        return _norm_text(raw)


def row_key(row) -> tuple:
    return (
        _norm_text(row["Name"]),
        _norm_symbol(row["Old Symbol"]),
        _norm_symbol(row["New Symbol"]),
        _norm_date(row["Date of change"]),
    )


# ── Loaders ────────────────────────────────────────────────────────────
def load_local() -> pd.DataFrame:
    df = pd.read_csv(
        LOCAL_FILE, dtype=str, keep_default_na=False, encoding="utf-8-sig"
    )
    missing = [c for c in CORE_COLS if c not in df.columns]
    if missing:
        raise RuntimeError(f"Local file missing columns: {missing}")
    return df


def load_remote(blob: bytes) -> pd.DataFrame:
    # Remote has NO header → assign CORE_COLS by position
    return pd.read_csv(
        io.BytesIO(blob),
        header=None,
        names=CORE_COLS,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )


# ── Canonicalisation (used only when writing new rows) ────────────────
def canonicalise_dates(df: pd.DataFrame) -> pd.DataFrame:
    parsed = pd.to_datetime(
        df["Date of change"], dayfirst=True, errors="coerce", format="mixed"
    )
    df = df.copy()
    df["Date of change"] = parsed.dt.strftime(DATE_FORMAT).fillna(df["Date of change"])
    return df


# ── Main ───────────────────────────────────────────────────────────────
def main() -> int:
    if not LOCAL_FILE.exists():
        print(f"❌ {LOCAL_FILE} not found. Commit an initial copy first.")
        return 1

    print(f"Remote : {NSE_CSV_URL}")
    print(f"Local  : {LOCAL_FILE}")

    try:
        blob = fetch_remote(NSE_CSV_URL)
    except Exception as exc:
        print(f"❌ Fetch failed: {exc}")
        return 1

    df_local  = load_local()
    df_remote = load_remote(blob)

    local_keys  = set(df_local.apply(row_key, axis=1))
    remote_keys = set(df_remote.apply(row_key, axis=1))

    only_remote = remote_keys - local_keys
    only_local  = local_keys - remote_keys

    print(f"\nLocal unique : {len(local_keys)}")
    print(f"Remote unique: {len(remote_keys)}")
    print(f"➕ only in remote: {len(only_remote)}")
    print(f"➖ only in local : {len(only_local)}")

    if not only_remote:
        print("\n✅ Nothing new. Local file untouched.")
        return 0

    # Filter the remote rows that aren't already represented locally
    mask_new     = ~df_remote.apply(row_key, axis=1).isin(local_keys)
    new_rows_raw = df_remote.loc[mask_new].copy()

    # Fit new rows to local schema (extra local cols → blank)
    new_rows_full = pd.DataFrame(columns=df_local.columns)
    for c in df_local.columns:
        new_rows_full[c] = new_rows_raw[c].values if c in new_rows_raw.columns else ""

    merged = pd.concat([df_local, new_rows_full], ignore_index=True)

    # Canonicalise dates on the *whole* file so everything is uniform
    merged = canonicalise_dates(merged)

    if SORT_OUTPUT:
        merged = merged.sort_values(
            by=["Name", "Date of change"],
            key=lambda s: s.str.lower() if s.name == "Name" else s,
        ).reset_index(drop=True)

    # Backup + write
    BACKUP_FILE.write_bytes(LOCAL_FILE.read_bytes())
    merged.to_csv(LOCAL_FILE, index=False)

    print(f"\n✅ Added {len(new_rows_raw)} row(s).")
    print(f"   {len(df_local)} → {len(merged)} rows.")
    print(f"   Backup: {BACKUP_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())