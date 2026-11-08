"""
NSE Index Constituents Updater

Fetches the latest official constituent lists from niftyindices.com,
compares them against the last row of each historical file, and appends
a new row only if the composition has changed.

File format (matches existing S&P/Nasdaq tracker):
    date<TAB>tickers
    4/30/2013<TAB>ADANIPORTS,APOLLOHOSP,...
"""

import csv
import io
import os
import re
import sys
from datetime import datetime
import requests

# ─── Configuration ───────────────────────────────────────────────────────
INDICES = {
    "Nifty_50.csv":              "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "Nifty_100.csv":             "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv",
    "Nifty_200.csv":             "https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv",
    "Nifty_500.csv":             "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "Nifty_Midcap_50.csv":       "https://www.niftyindices.com/IndexConstituent/ind_niftymidcap50list.csv",
    "Nifty_Midcap_100.csv":      "https://www.niftyindices.com/IndexConstituent/ind_niftymidcap100list.csv",
    "Nifty_Midcap_150.csv":      "https://www.niftyindices.com/IndexConstituent/ind_niftymidcap150list.csv",
    "Nifty_MidSmallcap_400.csv": "https://www.niftyindices.com/IndexConstituent/ind_niftymidsmallcap400list.csv",
    "Nifty_Next_50.csv":         "https://www.niftyindices.com/IndexConstituent/ind_niftynext50list.csv",
    "Nifty_Smallcap_50.csv":     "https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap50list.csv",
    "Nifty_Smallcap_100.csv":    "https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap100list.csv",
    "Nifty_Smallcap_250.csv":    "https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap250list.csv",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://niftyindices.com/",
    "Accept":  "text/csv,*/*",
}

DATA_DIR = os.path.dirname(os.path.abspath(__file__))


# ─── Helpers ─────────────────────────────────────────────────────────────
def fetch_symbols(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    if r.text.lstrip().startswith("<"):
        raise ValueError("HTML returned instead of CSV (WAF block)")
    reader = csv.DictReader(io.StringIO(r.text))
    symbols = [row["Symbol"].strip() for row in reader if row.get("Symbol")]
    if not symbols:
        raise ValueError("No symbols parsed from NSE CSV")
    return sorted(symbols)


def detect_delimiter(path):
    """Return '\t' or ',' depending on what the file actually uses."""
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        first = f.readline()
    if "\t" in first:
        return "\t"
    if "," in first:
        return ","
    # Last-resort: assume tab
    return "\t"


def looks_like_date(s):
    s = (s or "").strip()
    return bool(re.match(r"^\d{1,4}[/-]\d{1,2}[/-]\d{1,4}$", s))


def read_last_row(path):
    """Return the last valid (date, tickers) row, robust to delimiters."""
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        content = f.read()

    if not content.strip():
        return None

    delim = detect_delimiter(path)
    reader = csv.reader(io.StringIO(content), delimiter=delim)
    rows = [r for r in reader if r and len(r) >= 2]

    if not rows:
        return None

    # If the first row's first cell doesn't look like a date, treat it as header
    if not looks_like_date(rows[0][0]):
        rows = rows[1:]

    return rows[-1] if rows else None


def append_row(path, date_str, tickers):
    delim = detect_delimiter(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=delim)
        writer.writerow([date_str, tickers])


# ─── Main ────────────────────────────────────────────────────────────────
def main():
    changed, errors = [], []

    for filename, url in INDICES.items():
        path = os.path.join(DATA_DIR, filename)
        try:
            new_symbols = fetch_symbols(url)
            new_tickers = ",".join(new_symbols)

            last = read_last_row(path)
            if last is None:
                # Diagnose: show first 200 bytes so the log reveals the real format
                with open(path, "rb") as f:
                    head = f.read(200)
                raise ValueError(f"No data rows. File head = {head!r}")

            last_tickers = last[1].strip()

            if last_tickers == new_tickers:
                print(f"  [{filename}] no change")
            else:
                now = datetime.now()
                today = f"{now.month}/{now.day}/{now.year}"
                append_row(path, today, new_tickers)
                print(f"  [{filename}] CHANGED → appended row for {today}")
                changed.append(filename)

        except Exception as e:
            print(f"  [{filename}] ERROR: {e}")
            errors.append(filename)

    print()
    print(f"Summary: {len(INDICES)} checked, {len(changed)} changed, {len(errors)} errors")
    if changed:
        print(f"Changed: {', '.join(changed)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
