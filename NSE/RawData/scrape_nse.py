#!/usr/bin/env python3
"""
Daily NSE delisted companies scraper (GitHub Actions).

Outputs (both in this script's folder):
  nse_delisted_delisted.csv    full-overwrite snapshot — current truth
  nse_delisted_changelog.csv   append-only audit log — what changed when

Discovery strategies (tried in order, first success wins):
  1. Playwright headless browser  → JS-rendered page
  2. Plain requests               → grep HTML for xlsx URL
  3. Cached last-known URL        → fallback if NSE blocks cloud IPs
"""
import sys
import io
import re
import json
import time
from datetime import date
from pathlib import Path

import requests
import pandas as pd

# ─── Paths anchored to THIS file's directory ────────────────────────
SCRIPT_DIR    = Path(__file__).resolve().parent
OUTPUT_CSV    = SCRIPT_DIR / "nse_delisted_delisted.csv"
CHANGELOG_CSV = SCRIPT_DIR / "nse_delisted_changelog.csv"
STATE_FILE    = SCRIPT_DIR / ".nse_delisted_state.json"

# ─── Constants ──────────────────────────────────────────────────────
TARGET_URL     = "https://www.nseindia.com/static/list/list-of-companies-proposed-to-be-delisted"
TARGET_TEXT    = "List of Companies Delisted from NSE"
MIN_ROWS       = 300
MAX_CACHE_DAYS = 30

# Composite key for row identity across runs.
# Symbol alone is insufficient (VIKASHMET appears twice as different companies).
# Symbol + Delisted Date uniquely identifies a delisting event.
KEY_COLS  = ["Symbol", "ISIN", "Delisted Date"]
DATA_COLS = ["Company Name", "Board", "Type of Delisting"]

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection":      "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


# ─── Discovery ──────────────────────────────────────────────────────
def discover_via_playwright() -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright not installed, skipping")
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=HEADERS["User-Agent"], locale="en-US")
            page = ctx.new_page()
            page.goto("https://www.nseindia.com/", wait_until="domcontentloaded", timeout=30_000)
            page.goto(TARGET_URL, wait_until="networkidle", timeout=45_000)
            links = page.eval_on_selector_all(
                "a",
                """els => els
                    .filter(el => el.href && el.href.toLowerCase().endsWith('.xlsx'))
                    .map(el => ({text: el.innerText.trim(), href: el.href}))"""
            )
            browser.close()
        for link in links:
            if TARGET_TEXT in link["text"]:
                return link["href"]
        print(f"  no matching link. Saw: {[l['text'] for l in links]}")
    except Exception as e:
        print(f"  playwright failed: {e}")
    return None


def discover_via_requests() -> str | None:
    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        s.get("https://www.nseindia.com/", timeout=10)
        r = s.get(TARGET_URL, timeout=20)
        r.raise_for_status()
        urls = re.findall(r'https?://[^"\'\s<>]+\.xlsx', r.text, re.IGNORECASE)
        candidates = [u for u in urls if "delisted" in u.lower() and "proposed" not in u.lower()]
        if candidates:
            return candidates[0]
        print("  no xlsx URL in HTML (likely JS-rendered)")
    except Exception as e:
        print(f"  requests failed: {e}")
    return None


def load_cached_url() -> str | None:
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        age_days = (time.time() - data.get("fetched_at", 0)) / 86400
        if data.get("last_url") and age_days < MAX_CACHE_DAYS:
            print(f"  cache is {age_days:.1f} days old, usable")
            return data["last_url"]
        print(f"  cache too old ({age_days:.1f} days)")
    except FileNotFoundError:
        print("  no cache file")
    except Exception as e:
        print(f"  cache read failed: {e}")
    return None


# ─── Downloader ─────────────────────────────────────────────────────
def download(url: str, retries: int = 3) -> bytes | None:
    url = re.sub(r'(?<!:)//+', '/', url)
    for attempt in range(1, retries + 1):
        try:
            s = requests.Session()
            s.headers.update(HEADERS)
            s.get("https://www.nseindia.com/", timeout=10)
            r = s.get(url, timeout=60)
            r.raise_for_status()
            if len(r.content) < 5_000:
                raise ValueError(f"suspiciously small ({len(r.content)} bytes)")
            return r.content
        except Exception as e:
            print(f"  attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(2 ** attempt)
    return None


# ─── Parser ─────────────────────────────────────────────────────────
def parse_and_clean(content: bytes) -> pd.DataFrame:
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


# ─── Changelog diff ─────────────────────────────────────────────────
def _key_of(row: dict) -> tuple:
    return tuple(str(row.get(c) or "").strip() for c in KEY_COLS)


def _row_summary(row: dict) -> str:
    """One-line representation of a full row, for ADDED/REMOVED entries."""
    return " | ".join(str(row.get(c) or "") for c in KEY_COLS + DATA_COLS)


def compute_changes(old_df: pd.DataFrame | None, new_df: pd.DataFrame) -> list[dict]:
    """Return changelog entries describing old_df → new_df transition."""
    if old_df is None:
        return []   # first run — nothing to diff against

    today = date.today().isoformat()

    old_lookup = {_key_of(r): r for _, r in old_df.iterrows()}
    new_lookup = {_key_of(r): r for _, r in new_df.iterrows()}

    changes: list[dict] = []

    # Additions and updates
    for k, new_row in new_lookup.items():
        if k not in old_lookup:
            changes.append({
                "change_date":   today,
                "change_type":   "ADDED",
                "symbol":        new_row["Symbol"],
                "isin":          new_row["ISIN"],
                "delisted_date": new_row["Delisted Date"],
                "field":         "(row)",
                "old_value":     "",
                "new_value":     _row_summary(new_row),
            })
        else:
            old_row = old_lookup[k]
            for c in DATA_COLS:
                ov = str(old_row.get(c) or "").strip()
                nv = str(new_row.get(c) or "").strip()
                if ov != nv:
                    changes.append({
                        "change_date":   today,
                        "change_type":   "UPDATED",
                        "symbol":        new_row["Symbol"],
                        "isin":          new_row["ISIN"],
                        "delisted_date": new_row["Delisted Date"],
                        "field":         c,
                        "old_value":     ov,
                        "new_value":     nv,
                    })

    # Removals (NSE corrected a mistake, or the row was miscategorised)
    for k, old_row in old_lookup.items():
        if k not in new_lookup:
            changes.append({
                "change_date":   today,
                "change_type":   "REMOVED",
                "symbol":        old_row["Symbol"],
                "isin":          old_row["ISIN"],
                "delisted_date": old_row["Delisted Date"],
                "field":         "(row)",
                "old_value":     _row_summary(old_row),
                "new_value":     "",
            })

    return changes


def append_changelog(changes: list[dict]) -> None:
    if not changes:
        return
    new_entries = pd.DataFrame(changes)
    if CHANGELOG_CSV.exists():
        existing = pd.read_csv(CHANGELOG_CSV, dtype=str, keep_default_na=False)
        combined = pd.concat([existing, new_entries], ignore_index=True)
    else:
        combined = new_entries
    combined.to_csv(CHANGELOG_CSV, index=False)
    print(f"  📝 logged {len(changes)} change event(s) → {CHANGELOG_CSV.name}")


# ─── Main ───────────────────────────────────────────────────────────
def main() -> int:
    print(f"Working directory : {SCRIPT_DIR}")
    print(f"Current snapshot  : {OUTPUT_CSV}")
    print(f"Changelog         : {CHANGELOG_CSV}")

    # Load previous snapshot BEFORE overwriting (for diffing)
    old_df = None
    if OUTPUT_CSV.exists():
        try:
            old_df = pd.read_csv(OUTPUT_CSV, dtype=str, keep_default_na=False)
            print(f"  previous snapshot: {len(old_df)} rows loaded for diffing")
        except Exception as e:
            print(f"  ⚠️ could not load previous snapshot: {e} (changelog will skip this run)")

    print("\nStep 1: discovering download URL")
    url = discover_via_playwright() or discover_via_requests() or load_cached_url()
    if not url:
        print("ERROR: all discovery strategies failed", file=sys.stderr)
        return 1
    print(f"  URL: {url}")

    print("Step 2: downloading")
    content = download(url)
    if not content:
        print("ERROR: download failed", file=sys.stderr)
        return 1
    print(f"  {len(content):,} bytes")

    print("Step 3: parsing")
    try:
        df = parse_and_clean(content)
    except Exception as e:
        print(f"ERROR: parse failed: {e}", file=sys.stderr)
        return 1
    print(f"  {len(df)} rows")

    if len(df) < MIN_ROWS:
        print(f"ERROR: quality gate failed ({len(df)} < {MIN_ROWS})", file=sys.stderr)
        return 1

    print("Step 4: computing changes vs previous snapshot")
    changes = compute_changes(old_df, df)
    if old_df is None:
        print("  first run — no changelog entries (baseline established)")
    elif not changes:
        print("  no changes detected")
    else:
        added   = sum(1 for c in changes if c["change_type"] == "ADDED")
        removed = sum(1 for c in changes if c["change_type"] == "REMOVED")
        updated = sum(1 for c in changes if c["change_type"] == "UPDATED")
        print(f"  {added} added, {removed} removed, {updated} field updates")

    print(f"Step 5: writing snapshot → {OUTPUT_CSV.name}")
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_CSV, index=False)

    print("Step 6: appending to changelog")
    append_changelog(changes)

    # Persist last-known URL
    with open(STATE_FILE, "w") as f:
        json.dump({"last_url": url, "fetched_at": time.time()}, f)

    print("\nDone.")
    print(df.head().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
