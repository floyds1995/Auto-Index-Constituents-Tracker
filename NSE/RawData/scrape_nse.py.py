#!/usr/bin/env python3
"""
Daily NSE delisted companies scraper (GitHub Actions).
Output: NSE/RawData/nse_delisted_delisted.csv  (this script's folder)

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
from pathlib import Path

import requests
import pandas as pd

# ─── All paths are anchored to THIS file's directory ────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent
OUTPUT_CSV   = SCRIPT_DIR / "nse_delisted_delisted.csv"
STATE_FILE   = SCRIPT_DIR / ".nse_delisted_state.json"

# ─── Constants ──────────────────────────────────────────────────────
TARGET_URL    = "https://www.nseindia.com/static/list/list-of-companies-proposed-to-be-delisted"
TARGET_TEXT   = "List of Companies Delisted from NSE"
MIN_ROWS      = 300          # quality gate — refuse to overwrite below this
MAX_CACHE_DAYS = 30          # ignore cached URL older than this

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


# ─── Discovery strategies ────────────────────────────────────────────
def discover_via_playwright() -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright not installed, skipping")
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=HEADERS["User-Agent"], locale="en-US"
            )
            page = ctx.new_page()
            # Prime cookies on the homepage first
            page.goto(
                "https://www.nseindia.com/",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
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
        s.get("https://www.nseindia.com/", timeout=10)      # prime cookies
        r = s.get(TARGET_URL, timeout=20)
        r.raise_for_status()
        urls = re.findall(r'https?://[^"\'\s<>]+\.xlsx', r.text, re.IGNORECASE)
        candidates = [
            u for u in urls
            if "delisted" in u.lower() and "proposed" not in u.lower()
        ]
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


# ─── Downloader ──────────────────────────────────────────────────────
def download(url: str, retries: int = 3) -> bytes | None:
    # NSE uses double-slash in their hrefs; collapse it
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


# ─── Parser ──────────────────────────────────────────────────────────
def parse_and_clean(content: bytes) -> pd.DataFrame:
    # Try the named sheet first, fall back to first sheet
    try:
        df_raw = pd.read_excel(io.BytesIO(content), sheet_name="delisted", header=None)
    except ValueError:
        df_raw = pd.read_excel(io.BytesIO(content), sheet_name=0, header=None)

    # Find the header row containing "Symbol"
    hdr = 0
    for i in range(min(10, len(df_raw))):
        vals = [str(v).strip() for v in df_raw.iloc[i].tolist()]
        if "Symbol" in vals:
            hdr = i
            break

    df = df_raw.iloc[hdr + 1:].copy()
    df.columns = [str(c).strip() for c in df_raw.iloc[hdr].tolist()]

    # Drop blank rows and the trailing "Notes:" row
    df = df.dropna(how="all")
    if "Symbol" in df.columns:
        df = df[df["Symbol"].astype(str).str.strip().replace("nan", "").ne("")]
    df = df.reset_index(drop=True)

    # Normalize text: strip <br>, collapse whitespace
    for c in df.select_dtypes(include="object").columns:
        df[c] = (
            df[c].astype(str)
                 .str.replace(r"<br\s*/?>", " ", regex=True)
                 .str.replace(r"\s+", " ", regex=True)
                 .str.strip()
                 .replace({"nan": None, "None": None, "": None})
        )

    # Normalize dates
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")

    return df


# ─── Main ────────────────────────────────────────────────────────────
def main() -> int:
    print(f"Working directory : {SCRIPT_DIR}")
    print(f"Output will go to : {OUTPUT_CSV}")

    print("\nStep 1: discovering download URL")
    url = (
        discover_via_playwright()
        or discover_via_requests()
        or load_cached_url()
    )
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
        print(
            f"ERROR: quality gate failed ({len(df)} < {MIN_ROWS})",
            file=sys.stderr,
        )
        return 1

    print(f"Step 4: saving → {OUTPUT_CSV}")
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_CSV, index=False)

    # Persist last-known URL for future fallback
    with open(STATE_FILE, "w") as f:
        json.dump({"last_url": url, "fetched_at": time.time()}, f)

    print("Done.")
    print(df.head().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())