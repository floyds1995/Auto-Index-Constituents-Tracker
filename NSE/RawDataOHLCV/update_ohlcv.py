#!/usr/bin/env python3
"""
Debug script: test bhavcopy download from GitHub Actions.
Triggered manually via workflow_dispatch.

Tests:
  1. New format URL (2019+) - the one currently failing
  2. Old format URL (pre-2019) - the one that works
  3. Response headers and content-encoding
  4. Parsing of both formats

Prints a clear PASS/FAIL summary at the end.
"""

import requests
import pandas as pd
from io import StringIO, BytesIO
import zipfile
import gzip
import sys

# ============================================================
# Headers — exactly as the production script uses them
# ============================================================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/csv,application/csv,application/xhtml+xml,"
              "application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

# NOTE: Accept-Encoding intentionally omitted.
# requests defaults to "gzip, deflate" which it can decompress.
# Adding "br" (Brotli) breaks things unless the `brotli` pip package is installed.

results = []

def divider(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)

def classify_response(r):
    """Return a short classification of the response body."""
    if r.status_code != 200:
        return f"HTTP {r.status_code}"
    if len(r.content) == 0:
        return "empty body"
    head = r.content[:8]
    if head[:4] == b"PK\x03\x04":
        return "valid zip"
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head[:15].lstrip().startswith(b"<"):
        return "html page"
    if b"," in r.content[:200] and b"SYMBOL" in r.content[:200]:
        return "valid CSV"
    if b"|" in r.content[:200]:
        return "pipe-delimited"
    if r.content[:3] == b"\x1b\x0c\x00" or head.startswith(b"\x1b"):
        return "brotli/compressed binary — HEADER ISSUE"
    return "unknown"

# ============================================================
# TEST 1: New format URL (2019+)
# ============================================================
divider("TEST 1: NEW format URL (should work)")
url_new = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_21092026.csv"
print(f"URL: {url_new}")

try:
    r = requests.get(url_new, headers=HEADERS, timeout=30)
    print(f"Status:          {r.status_code}")
    print(f"Bytes:           {len(r.content):,}")
    print(f"Content-Type:    {r.headers.get('Content-Type')}")
    print(f"Content-Encoding:{r.headers.get('Content-Encoding')}")
    print(f"First 80 chars:  {r.content[:80]!r}")
    print(f"Classification:  {classify_response(r)}")

    if r.status_code == 200 and len(r.content) > 1000:
        try:
            df = pd.read_csv(StringIO(r.text))
            df.columns = [c.strip() for c in df.columns]
            print(f"\nParsed rows:     {len(df):,}")
            print(f"Columns:         {df.columns.tolist()}")
            if "DATE1" in df.columns:
                d = pd.to_datetime(df["DATE1"].astype(str).str.strip(),
                                   format="%d-%b-%Y", errors="coerce")
                print(f"DATE1 NaT count: {d.isna().sum()} / {len(d)}")
                print(f"DATE1 sample:    {df['DATE1'].head(3).tolist()}")
                if d.isna().sum() == 0:
                    results.append(("NEW format", "PASS", f"{len(df)} rows parsed cleanly"))
                else:
                    results.append(("NEW format", "FAIL", f"{d.isna().sum()} NaT values"))
            else:
                results.append(("NEW format", "FAIL", "DATE1 column missing"))
        except Exception as e:
            print(f"\nParse error: {e}")
            results.append(("NEW format", "FAIL", f"parse error: {type(e).__name__}"))
    else:
        results.append(("NEW format", "FAIL", f"status {r.status_code}, bytes {len(r.content)}"))

except Exception as e:
    print(f"Request error: {e}")
    results.append(("NEW format", "FAIL", f"request error: {type(e).__name__}"))

# ============================================================
# TEST 2: Old format URL (pre-2019)
# ============================================================
divider("TEST 2: OLD format URL (known working)")
url_old = "https://nsearchives.nseindia.com/content/historical/EQUITIES/2015/DEC/cm15DEC2015bhav.csv.zip"
print(f"URL: {url_old}")

try:
    r = requests.get(url_old, headers=HEADERS, timeout=30)
    print(f"Status:          {r.status_code}")
    print(f"Bytes:           {len(r.content):,}")
    print(f"Content-Type:    {r.headers.get('Content-Type')}")
    print(f"Content-Encoding:{r.headers.get('Content-Encoding')}")
    print(f"First 4 bytes:   {r.content[:4]!r}")
    print(f"Classification:  {classify_response(r)}")

    if r.status_code == 200 and r.content[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(BytesIO(r.content)) as z:
                name = z.namelist()[0]
                print(f"Zip contains:    {name}")
                with z.open(name) as f:
                    df = pd.read_csv(f, sep="|")
            df.columns = [c.strip() for c in df.columns]
            print(f"\nParsed rows:     {len(df):,}")
            print(f"Columns:         {df.columns.tolist()}")
            results.append(("OLD format", "PASS", f"{len(df)} rows parsed cleanly"))
        except Exception as e:
            print(f"\nParse error: {e}")
            results.append(("OLD format", "FAIL", f"parse error: {type(e).__name__}"))
    else:
        results.append(("OLD format", "FAIL", f"status {r.status_code}, bytes {len(r.content)}"))

except Exception as e:
    print(f"Request error: {e}")
    results.append(("OLD format", "FAIL", f"request error: {type(e).__name__}"))

# ============================================================
# TEST 3: Confirm 'br' header breaks things (prove the theory)
# ============================================================
divider("TEST 3: Same URL with 'Accept-Encoding: br' (should fail)")
print("This reproduces the bug. If this test shows binary garbage but")
print("TEST 1 shows clean CSV, the fix is confirmed: remove 'br' from headers.")

headers_broken = dict(HEADERS)
headers_broken["Accept-Encoding"] = "gzip, deflate, br"

try:
    r = requests.get(url_new, headers=headers_broken, timeout=30)
    print(f"Status:          {r.status_code}")
    print(f"Bytes:           {len(r.content):,}")
    print(f"Content-Encoding:{r.headers.get('Content-Encoding')}")
    print(f"First 80 chars:  {r.content[:80]!r}")
    print(f"Classification:  {classify_response(r)}")

    if b"SYMBOL" in r.content[:200]:
        results.append(("Brotli test", "UNEXPECTED", "worked anyway — br may be OK now"))
    else:
        results.append(("Brotli test", "CONFIRMED", "br header causes binary garbage"))
except Exception as e:
    print(f"Request error: {e}")
    results.append(("Brotli test", "ERROR", str(e)))

# ============================================================
# SUMMARY
# ============================================================
divider("SUMMARY")
any_fail = False
for name, status, detail in results:
    icon = {"PASS": "✅", "FAIL": "❌", "CONFIRMED": "✅",
            "UNEXPECTED": "⚠️", "ERROR": "❌"}.get(status, "?")
    print(f"{icon}  {name:15s} {status:12s} {detail}")
    if status == "FAIL" or status == "ERROR":
        any_fail = True

print()
if any_fail:
    print("Verdict: at least one test failed. See details above.")
else:
    print("Verdict: all tests behaved as expected.")

# Exit code 0 so the workflow doesn't show a red X — this is a debug run.
sys.exit(0)
