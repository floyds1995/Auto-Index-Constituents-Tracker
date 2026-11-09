#!/usr/bin/env python3
"""
Unified NSE data pipeline.

Runs three independent data-collection tasks in sequence:

  1. Symbol Change Sync          → NSE/RawData/symbolchange.csv
  2. Corporate Actions Sync      → NSE/RawData/CorporateActions.csv
  3. Delisted Companies Scraper  → NSE/RawData/nse_delisted_*.csv

Each task preserves the exact behaviour of its original standalone script.
Failures in one task do not prevent the others from running; the process
exits non-zero if any task failed.

Exit codes:
  0  all tasks succeeded
  1  one or more tasks failed
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

# ── Logging ────────────────────────────────────────────────────────────
log = logging.getLogger("nse_pipeline")


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


# ── Shared helpers ─────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent  # NSE/RawData

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    """Write a DataFrame to CSV atomically (temp file + os.replace)."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    os.close(fd)
    try:
        df.to_csv(tmp, index=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ══════════════════════════════════════════════════════════════════════
# 1. SYMBOL CHANGE SYNC
# ══════════════════════════════════════════════════════════════════════
NSE_SYMBOL_CSV_URL = "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"
SYMBOL_LOCAL_FILE  = SCRIPT_DIR / "symbolchange.csv"
SYMBOL_BACKUP_FILE = SCRIPT_DIR / "symbolchange.csv.bak"
SYMBOL_DATE_FORMAT = "%d-%b-%Y"
SYMBOL_SORT_OUTPUT = True
SYMBOL_CORE_COLS   = ["Name", "Old Symbol", "New Symbol", "Date of change"]
SYMBOL_TIMEOUT     = 30


def symbol_fetch_remote(url: str) -> bytes:
    headers = {
        "User-Agent": BROWSER_HEADERS["User-Agent"],
        "Accept": "text/csv,application/csv,*/*",
        "Referer": "https://www.nseindia.com/",
    }
    r = requests.get(url, headers=headers, timeout=SYMBOL_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for {url}")

    # Encoding: prefer utf-8-sig, fall back to latin-1
    try:
        text = r.content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        log.warning("UTF-8 decode failed (%s); falling back to latin-1", e)
        text = r.content.decode("latin-1")

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise RuntimeError("Remote symbolchange.csv is empty or truncated.")

    # Validate ALL rows (not just first 20)
    bad_rows: list[tuple[int, int]] = []
    for i, ln in enumerate(lines):
        ncols = len(ln.split(","))
        if ncols != 4:
            bad_rows.append((i, ncols))
    if bad_rows:
        i0, n0 = bad_rows[0]
        raise RuntimeError(
            f"Structure validation failed: {len(bad_rows)} row(s) with wrong "
            f"column count (first: line {i0} has {n0} cols, expected 4)."
        )
    log.info("✅ Remote symbolchange OK: %d lines, 4 columns each.", len(lines))
    return r.content


def _sym_norm_text(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _sym_norm_symbol(s) -> str:
    t = _sym_norm_text(s).upper()
    if t.isdigit():
        return t.lstrip("0") or "0"
    return t


def _sym_norm_date(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    raw = str(s).strip()
    if not raw:
        return ""
    try:
        return pd.to_datetime(raw, dayfirst=True, errors="raise", format="mixed").strftime("%Y-%m-%d")
    except Exception:
        return _sym_norm_text(raw)


def _sym_row_key(row) -> tuple:
    return (
        _sym_norm_text(row["Name"]),
        _sym_norm_symbol(row["Old Symbol"]),
        _sym_norm_symbol(row["New Symbol"]),
        _sym_norm_date(row["Date of change"]),
    )


def _sym_load_local() -> pd.DataFrame:
    df = pd.read_csv(SYMBOL_LOCAL_FILE, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    missing = [c for c in SYMBOL_CORE_COLS if c not in df.columns]
    if missing:
        raise RuntimeError(f"Local symbolchange.csv missing columns: {missing}")
    return df


def _sym_load_remote(blob: bytes) -> pd.DataFrame:
    return pd.read_csv(
        io.BytesIO(blob), header=None, names=SYMBOL_CORE_COLS,
        dtype=str, keep_default_na=False, encoding="utf-8-sig",
    )


def _sym_canonicalise_dates(df: pd.DataFrame) -> pd.DataFrame:
    parsed = pd.to_datetime(df["Date of change"], dayfirst=True, errors="coerce", format="mixed")
    df = df.copy()
    df["Date of change"] = parsed.dt.strftime(SYMBOL_DATE_FORMAT).fillna(df["Date of change"])
    return df


def run_symbol_change() -> int:
    if not SYMBOL_LOCAL_FILE.exists():
        log.error("❌ %s not found. Commit an initial copy first.", SYMBOL_LOCAL_FILE)
        return 1

    log.info("Remote : %s", NSE_SYMBOL_CSV_URL)
    log.info("Local  : %s", SYMBOL_LOCAL_FILE)

    try:
        blob = symbol_fetch_remote(NSE_SYMBOL_CSV_URL)
    except Exception as exc:
        log.error("❌ Fetch failed: %s", exc)
        return 1

    try:
        df_local  = _sym_load_local()
        df_remote = _sym_load_remote(blob)
    except Exception as exc:
        log.error("❌ Parse failed: %s", exc)
        return 1

    local_keys  = set(df_local.apply(_sym_row_key, axis=1))
    remote_keys = set(df_remote.apply(_sym_row_key, axis=1))

    only_remote = remote_keys - local_keys
    only_local  = local_keys - remote_keys

    log.info("Local unique : %d", len(local_keys))
    log.info("Remote unique: %d", len(remote_keys))
    log.info("➕ only in remote: %d", len(only_remote))
    log.info("➖ only in local : %d", len(only_local))

    if not only_remote:
        log.info("✅ Nothing new. Local symbolchange.csv untouched.")
        return 0

    mask_new     = ~df_remote.apply(_sym_row_key, axis=1).isin(local_keys)
    new_rows_raw = df_remote.loc[mask_new].copy()

    # Row-level logging for traceability
    for _, r in new_rows_raw.iterrows():
        log.info("  + %s: %s → %s on %s",
                 r["Name"], r["Old Symbol"], r["New Symbol"], r["Date of change"])

    new_rows_full = pd.DataFrame(columns=df_local.columns)
    for c in df_local.columns:
        new_rows_full[c] = new_rows_raw[c].values if c in new_rows_raw.columns else ""

    merged = pd.concat([df_local, new_rows_full], ignore_index=True)
    merged = _sym_canonicalise_dates(merged)

    if SYMBOL_SORT_OUTPUT:
        merged = merged.sort_values(
            by=["Name", "Date of change"],
            key=lambda s: s.str.lower() if s.name == "Name" else s,
        ).reset_index(drop=True)

    # Backup + atomic write
    SYMBOL_BACKUP_FILE.write_bytes(SYMBOL_LOCAL_FILE.read_bytes())
    atomic_write_csv(merged, SYMBOL_LOCAL_FILE)

    log.info("✅ Added %d row(s). %d → %d rows. Backup: %s",
             len(new_rows_raw), len(df_local), len(merged), SYMBOL_BACKUP_FILE.name)
    return 0


# ══════════════════════════════════════════════════════════════════════
# 2. CORPORATE ACTIONS SYNC
# ══════════════════════════════════════════════════════════════════════
CA_API = "https://www.nseindia.com/api/corporates-corporateActions"
CA_HOME = "https://www.nseindia.com"
CA_WINDOW_DAYS = 7
CA_LOCAL_FILE  = SCRIPT_DIR / "CorporateActions.csv"
CA_BACKUP_FILE = SCRIPT_DIR / "CorporateActions.csv.bak"
CA_COLS = [
    "SYMBOL", "COMPANY NAME", "SERIES", "PURPOSE", "FACE VALUE",
    "EX-DATE", "RECORD DATE", "BOOK CLOSURE START DATE", "BOOK CLOSURE END DATE",
]
CA_DATE_COLS = {"EX-DATE", "RECORD DATE", "BOOK CLOSURE START DATE", "BOOK CLOSURE END DATE"}
CA_HEADERS = {
    "User-Agent": BROWSER_HEADERS["User-Agent"],
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-actions",
    "X-Requested-With": "XMLHttpRequest",
}


def _ca_t_series(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\s+", " ", regex=True).str.strip().str.lower()


def _ca_d_series(s: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(s, dayfirst=True, errors="coerce", format="mixed")
    return parsed.dt.strftime("%Y-%m-%d").fillna(_ca_t_series(s))


def _ca_row_key_series(df: pd.DataFrame) -> pd.Series:
    parts = [_ca_d_series(df[c]) if c in CA_DATE_COLS else _ca_t_series(df[c]) for c in CA_COLS]
    k = parts[0]
    for p in parts[1:]:
        k = k + "|" + p
    return k


def _ca_fetch_remote(days: int = CA_WINDOW_DAYS) -> pd.DataFrame:
    session = requests.Session()
    session.get(CA_HOME, headers=CA_HEADERS, timeout=15)
    today = datetime.now()
    params = {
        "index":     "equities",
        "from_date": (today - timedelta(days=days - 1)).strftime("%d-%m-%Y"),
        "to_date":   today.strftime("%d-%m-%Y"),
        "csv":       "true",
    }
    r = session.get(CA_API, headers=CA_HEADERS, params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for {CA_API}")

    df = pd.read_csv(
        io.StringIO(r.content.decode("utf-8", errors="replace")),
        dtype=str, keep_default_na=False,
    )
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in CA_COLS if c not in df.columns]
    if missing:
        raise RuntimeError(f"Remote corporate-actions response missing columns: {missing}")
    log.info("✅ Remote corporate-actions OK: %d rows for window %s → %s",
             len(df), params["from_date"], params["to_date"])
    return df


def run_corporate_actions() -> int:
    if not CA_LOCAL_FILE.exists():
        log.error("❌ %s not found. Commit an initial copy first.", CA_LOCAL_FILE)
        return 1

    try:
        df_local = pd.read_csv(CA_LOCAL_FILE, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except Exception as exc:
        log.error("❌ Could not read local file: %s", exc)
        return 1
    df_local.columns = [c.strip() for c in df_local.columns]
    log.info("📂 Local CorporateActions: %d rows (read as-is, never modified)", len(df_local))

    local_keys = set(_ca_row_key_series(df_local))

    try:
        df_remote = _ca_fetch_remote()
    except Exception as exc:
        log.error("❌ Fetch failed: %s", exc)
        return 1

    remote_keys = _ca_row_key_series(df_remote)
    mask_new    = ~remote_keys.isin(local_keys)
    df_new      = df_remote.loc[mask_new].copy()

    log.info("➕ new corporate-action rows: %d", len(df_new))

    if len(df_new) == 0:
        log.info("✅ Nothing new. CorporateActions.csv unchanged.")
        return 0

    for c in CA_COLS:
        if c not in df_new.columns:
            df_new[c] = ""

    merged = pd.concat([df_local[CA_COLS], df_new[CA_COLS]], ignore_index=True)

    CA_BACKUP_FILE.write_bytes(CA_LOCAL_FILE.read_bytes())
    merged.to_csv(CA_LOCAL_FILE, index=False)

    log.info("✅ Wrote %s: %d → %d (+%d)",
             CA_LOCAL_FILE.name, len(df_local), len(merged), len(df_new))
    return 0


# ══════════════════════════════════════════════════════════════════════
# 3. DELISTED COMPANIES SCRAPER
# ══════════════════════════════════════════════════════════════════════
DEL_OUTPUT_CSV    = SCRIPT_DIR / "nse_delisted_delisted.csv"
DEL_CHANGELOG_CSV = SCRIPT_DIR / "nse_delisted_changelog.csv"
DEL_STATE_FILE    = SCRIPT_DIR / ".nse_delisted_state.json"

DEL_TARGET_URL  = "https://www.nseindia.com/static/list/list-of-companies-proposed-to-be-delisted"
DEL_TARGET_TEXT = "List of Companies Delisted from NSE"
DEL_MIN_ROWS    = 300
DEL_MAX_CACHE_DAYS = 3
DEL_MAX_STALE_RUNS = 3

DEL_KEY_COLS  = ["Symbol", "ISIN", "Delisted Date"]
DEL_DATA_COLS = ["Company Name", "Board", "Type of Delisting"]

DEL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def _del_discover_via_playwright() -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.info("  playwright not installed, skipping")
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=DEL_HEADERS["User-Agent"], locale="en-US")
            page = ctx.new_page()
            page.goto("https://www.nseindia.com/", wait_until="domcontentloaded", timeout=30_000)
            page.goto(DEL_TARGET_URL, wait_until="networkidle", timeout=45_000)
            links = page.eval_on_selector_all(
                "a",
                """els => els
                    .filter(el => el.href && el.href.toLowerCase().endsWith('.xlsx'))
                    .map(el => ({text: el.innerText.trim(), href: el.href}))""",
            )
            browser.close()
        for link in links:
            if DEL_TARGET_TEXT in link["text"]:
                return link["href"]
        log.info("  no matching link. Saw: %s", [l["text"] for l in links])
    except Exception as e:
        log.warning("  playwright failed: %s", e)
    return None


def _del_discover_via_requests() -> str | None:
    try:
        s = requests.Session()
        s.headers.update(DEL_HEADERS)
        s.get("https://www.nseindia.com/", timeout=10)
        r = s.get(DEL_TARGET_URL, timeout=20)
        r.raise_for_status()
        urls = re.findall(r'https?://[^"\'\s<>]+\.xlsx', r.text, re.IGNORECASE)
        candidates = [u for u in urls if "delisted" in u.lower() and "proposed" not in u.lower()]
        if candidates:
            return candidates[0]
        log.info("  no xlsx URL in HTML (likely JS-rendered)")
    except Exception as e:
        log.warning("  requests failed: %s", e)
    return None


def _del_load_cached_url() -> str | None:
    try:
        with open(DEL_STATE_FILE) as f:
            data = json.load(f)
    except FileNotFoundError:
        log.info("  no cache file")
        return None
    except Exception as e:
        log.warning("  cache read failed: %s", e)
        return None

    age_days = (time.time() - data.get("fetched_at", 0)) / 86400
    if not data.get("last_url"):
        log.info("  cache file has no last_url")
        return None
    if age_days >= DEL_MAX_CACHE_DAYS:
        log.info("  cache too old (%.1f days >= %d) — refusing", age_days, DEL_MAX_CACHE_DAYS)
        return None

    stale_runs = int(data.get("stale_runs", 0)) + 1
    data["stale_runs"] = stale_runs
    try:
        with open(DEL_STATE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log.warning("  could not persist stale counter: %s", e)

    log.warning("  ⚠️  USING STALE CACHE — %.1f days old, stale_runs=%d/%d",
                age_days, stale_runs, DEL_MAX_STALE_RUNS)
    log.warning("      NSE may be blocking; data could be outdated.")

    if stale_runs >= DEL_MAX_STALE_RUNS:
        raise RuntimeError(
            f"Cache used {stale_runs} runs in a row — NSE block suspected. "
            "Refusing to continue serving stale data."
        )
    return data["last_url"]


def _del_download(url: str, retries: int = 3) -> bytes | None:
    url = re.sub(r"(?<!:)//+", "/", url)
    for attempt in range(1, retries + 1):
        try:
            s = requests.Session()
            s.headers.update(DEL_HEADERS)
            s.get("https://www.nseindia.com/", timeout=10)
            r = s.get(url, timeout=60)
            r.raise_for_status()
            if len(r.content) < 5_000:
                raise ValueError(f"suspiciously small ({len(r.content)} bytes)")
            return r.content
        except Exception as e:
            log.warning("  attempt %d/%d: %s", attempt, retries, e)
            if attempt < retries:
                time.sleep(2 ** attempt)
    return None


def _del_parse_and_clean(content: bytes) -> pd.DataFrame:
    try:
        df_raw = pd.read_excel(io.BytesIO(content), sheet_name="delisted", header=None)
    except ValueError:
        df_raw = pd.read_excel(io.BytesIO(content), sheet_name=0, header=None)

    hdr = 0
    for i in range(min(10, len(df_raw))):
        if "Symbol" in [str(v).strip() for v in df_raw.iloc[i].tolist()]:
            hdr = i
            break

    df = df_raw.iloc[hdr + 1:].copy()
    df.columns = [str(c).strip() for c in df_raw.iloc[hdr].tolist()]

    df = df.dropna(how="all")
    if "Symbol" in df.columns:
        df = df[df["Symbol"].astype(str).str.strip().replace("nan", "").ne("")]
    df = df.reset_index(drop=True)

    for c in df.select_dtypes(include="object").columns:
        df[c] = (
            df[c].astype(str)
                 .str.replace(r"<br\s*/?>", " ", regex=True)
                 .str.replace(r"\s+", " ", regex=True)
                 .str.strip()
                 .replace({"nan": None, "None": None, "": None})
        )

    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
    return df


def _del_key_of(row: dict) -> tuple:
    return tuple(str(row.get(c) or "").strip() for c in DEL_KEY_COLS)


def _del_row_summary(row: dict) -> str:
    return " | ".join(str(row.get(c) or "") for c in DEL_KEY_COLS + DEL_DATA_COLS)


def _del_compute_changes(old_df: pd.DataFrame | None, new_df: pd.DataFrame) -> list[dict]:
    if old_df is None:
        return []
    today = date.today().isoformat()
    old_lookup = {_del_key_of(r): r for _, r in old_df.iterrows()}
    new_lookup = {_del_key_of(r): r for _, r in new_df.iterrows()}
    changes: list[dict] = []

    for k, new_row in new_lookup.items():
        if k not in old_lookup:
            changes.append({
                "change_date": today, "change_type": "ADDED",
                "symbol": new_row["Symbol"], "isin": new_row["ISIN"],
                "delisted_date": new_row["Delisted Date"], "field": "(row)",
                "old_value": "", "new_value": _del_row_summary(new_row),
            })
        else:
            old_row = old_lookup[k]
            for c in DEL_DATA_COLS:
                ov = str(old_row.get(c) or "").strip()
                nv = str(new_row.get(c) or "").strip()
                if ov != nv:
                    changes.append({
                        "change_date": today, "change_type": "UPDATED",
                        "symbol": new_row["Symbol"], "isin": new_row["ISIN"],
                        "delisted_date": new_row["Delisted Date"], "field": c,
                        "old_value": ov, "new_value": nv,
                    })

    for k, old_row in old_lookup.items():
        if k not in new_lookup:
            changes.append({
                "change_date": today, "change_type": "REMOVED",
                "symbol": old_row["Symbol"], "isin": old_row["ISIN"],
                "delisted_date": old_row["Delisted Date"], "field": "(row)",
                "old_value": _del_row_summary(old_row), "new_value": "",
            })
    return changes


def _del_append_changelog(changes: list[dict]) -> None:
    if not changes:
        return
    new_entries = pd.DataFrame(changes)
    if DEL_CHANGELOG_CSV.exists():
        existing = pd.read_csv(DEL_CHANGELOG_CSV, dtype=str, keep_default_na=False)
        combined = pd.concat([existing, new_entries], ignore_index=True)
    else:
        combined = new_entries
    combined.to_csv(DEL_CHANGELOG_CSV, index=False)
    log.info("  📝 logged %d change event(s) → %s", len(changes), DEL_CHANGELOG_CSV.name)


def run_delisted_scraper() -> int:
    log.info("Output dir : %s", SCRIPT_DIR)
    log.info("Snapshot   : %s", DEL_OUTPUT_CSV.name)
    log.info("Changelog  : %s", DEL_CHANGELOG_CSV.name)

    old_df = None
    if DEL_OUTPUT_CSV.exists():
        try:
            old_df = pd.read_csv(DEL_OUTPUT_CSV, dtype=str, keep_default_na=False)
            log.info("  previous snapshot: %d rows loaded for diffing", len(old_df))
        except Exception as e:
            log.warning("  ⚠️ could not load previous snapshot: %s", e)

    log.info("Step 1: discovering download URL")
    try:
        url = _del_discover_via_playwright() or _del_discover_via_requests() or _del_load_cached_url()
    except RuntimeError as e:
        log.error("ERROR: %s", e)
        return 1
    if not url:
        log.error("ERROR: all discovery strategies failed")
        return 1
    log.info("  URL: %s", url)

    log.info("Step 2: downloading")
    content = _del_download(url)
    if not content:
        log.error("ERROR: download failed")
        return 1
    log.info("  %s bytes", f"{len(content):,}")

    log.info("Step 3: parsing")
    try:
        df = _del_parse_and_clean(content)
    except Exception as e:
        log.error("ERROR: parse failed: %s", e)
        return 1
    log.info("  %d rows", len(df))

    if len(df) < DEL_MIN_ROWS:
        log.error("ERROR: quality gate failed (%d < %d)", len(df), DEL_MIN_ROWS)
        return 1

    log.info("Step 4: computing changes vs previous snapshot")
    changes = _del_compute_changes(old_df, df)
    if old_df is None:
        log.info("  first run — no changelog entries (baseline established)")
    elif not changes:
        log.info("  no changes detected")
    else:
        added   = sum(1 for c in changes if c["change_type"] == "ADDED")
        removed = sum(1 for c in changes if c["change_type"] == "REMOVED")
        updated = sum(1 for c in changes if c["change_type"] == "UPDATED")
        log.info("  %d added, %d removed, %d field updates", added, removed, updated)

    log.info("Step 5: writing snapshot → %s", DEL_OUTPUT_CSV.name)
    DEL_OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(DEL_OUTPUT_CSV, index=False)

    log.info("Step 6: appending to changelog")
    _del_append_changelog(changes)

    with open(DEL_STATE_FILE, "w") as f:
        json.dump({"last_url": url, "fetched_at": time.time(), "stale_runs": 0}, f)

    log.info("Done. First 5 rows:")
    for line in df.head().to_string().splitlines():
        log.info("  %s", line)
    return 0


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════
def main() -> int:
    setup_logging()
    tasks = [
        ("symbol_change",     run_symbol_change),
        ("corporate_actions", run_corporate_actions),
        ("delisted_scraper",  run_delisted_scraper),
    ]
    results: dict[str, int] = {}
    t0 = time.time()
    for name, fn in tasks:
        log.info("=" * 70)
        log.info("▶  TASK: %s", name)
        log.info("=" * 70)
        t = time.time()
        try:
            results[name] = fn()
        except Exception:
            log.exception("Unhandled exception in task %r", name)
            results[name] = 1
        log.info("◀  TASK %s: %s (%.1fs)",
                 name, "OK" if results[name] == 0 else "FAILED", time.time() - t)

    log.info("=" * 70)
    log.info("SUMMARY (total %.1fs)", time.time() - t0)
    log.info("=" * 70)
    for name, code in results.items():
        log.info("  %-20s %s", name, "OK" if code == 0 else "FAILED")

    return 0 if all(c == 0 for c in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())