# Auto-Index-Constituents-Tracker

![SNP500](https://github.com/floyds1995/Auto-Index-Constituents-Tracker/actions/workflows/snp500.yml/badge.svg)
[![Last Run](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.github.com%2Frepos%2Ffloyds1995%2FAuto-Index-Constituents-Tracker%2Factions%2Fworkflows%2F361300964%2Fruns%3Fstatus%3Dcompleted%26per_page%3D1&query=%24.workflow_runs%5B0%5D.run_started_at&label=last%20run&color=blue)](https://github.com/floyds1995/Auto-Index-Constituents-Tracker/actions/workflows/snp500.yml)

![NDX100](https://github.com/floyds1995/Auto-Index-Constituents-Tracker/actions/workflows/ndx100.yml/badge.svg)
[![Last Run](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.github.com%2Frepos%2Ffloyds1995%2FAuto-Index-Constituents-Tracker%2Factions%2Fworkflows%2F361301148%2Fruns%3Fstatus%3Dcompleted%26per_page%3D1&query=%24.workflow_runs%5B0%5D.run_started_at&label=last%20run&color=blue)](https://github.com/floyds1995/Auto-Index-Constituents-Tracker/actions/workflows/ndx100.yml)

![US Data OHLCV](https://github.com/floyds1995/Auto-Index-Constituents-Tracker/actions/workflows/US_ohlcv.yml/badge.svg)
[![Last Run](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.github.com%2Frepos%2Ffloyds1995%2FAuto-Index-Constituents-Tracker%2Factions%2Fworkflows%2F363577729%2Fruns%3Fstatus%3Dcompleted%26per_page%3D1&query=%24.workflow_runs%5B0%5D.run_started_at&label=last%20run&color=blue)](https://github.com/floyds1995/Auto-Index-Constituents-Tracker/actions/workflows/US_ohlcv.yml)

![NSE_Constituents](https://github.com/floyds1995/Auto-Index-Constituents-Tracker/actions/workflows/nse.yml/badge.svg)
[![Last Run](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.github.com%2Frepos%2Ffloyds1995%2FAuto-Index-Constituents-Tracker%2Factions%2Fworkflows%2F361282030%2Fruns%3Fstatus%3Dcompleted%26per_page%3D1&query=%24.workflow_runs%5B0%5D.run_started_at&label=last%20run&color=blue)](https://github.com/floyds1995/Auto-Index-Constituents-Tracker/actions/workflows/nse.yml)

![NSE OHLCV](https://github.com/floyds1995/Auto-Index-Constituents-Tracker/actions/workflows/nse_ohlcv.yml/badge.svg)
[![Last Run](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.github.com%2Frepos%2Ffloyds1995%2FAuto-Index-Constituents-Tracker%2Factions%2Fworkflows%2F363577729%2Fruns%3Fstatus%3Dcompleted%26per_page%3D1&query=%24.workflow_runs%5B0%5D.run_started_at&label=last%20run&color=blue)](https://github.com/floyds1995/Auto-Index-Constituents-Tracker/actions/workflows/nse_ohlcv.yml)

![NSE_Corporate Data](https://github.com/floyds1995/Auto-Index-Constituents-Tracker/actions/workflows/nse_daily_data.yaml/badge.svg)
[![Last Run](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.github.com%2Frepos%2Ffloyds1995%2FAuto-Index-Constituents-Tracker%2Factions%2Fworkflows%2F363168871%2Fruns%3Fstatus%3Dcompleted%26per_page%3D1&query=%24.workflow_runs%5B0%5D.run_started_at&label=last%20run&color=blue)](https://github.com/floyds1995/Auto-Index-Constituents-Tracker/actions/workflows/nse_daily_data.yaml)

Automated daily tracker for historical index constituents, NSE reference data, and equity OHLCV. Currently supports S&P 500, Nasdaq 100, 12 NSE indices (Nifty 50 through Nifty Smallcap 250), two NSE reference archives (symbol changes and corporate actions), the complete NSE equity EOD OHLCV history since 1995, and US equity EOD OHLCV covering **2019-01-01 → present** for the current S&P 500 and Nasdaq-100 universe.

Each dataset is stored as a single CSV (or, for OHLCV, Parquet files). GitHub Actions runs on a schedule, fetches the latest data from the official source, and appends new rows only when something actually changed.

No servers. No cron jobs. No external services — just GitHub Actions, Wikipedia, NSE's official endpoints, and **Yahoo Finance (via `yfinance`)**.

## What It Does

### Index constituents

Every tracked index is stored in this format:

```
date,tickers
1996-01-02,"AAL,AAMRQ,AAPL,ABI,..."
1996-01-03,"AAL,AAMRQ,AAPL,ABI,..."
...
2026-08-18,"AAPL,ABNB,ADBE,..."
```

- One row per date the composition changed — not every trading day
- `tickers` is the full, sorted, comma-separated list of constituents on that date
- The last row always represents the current composition
- S&P 500 and Nasdaq 100 use ISO dates (`YYYY-MM-DD`); NSE files use `M/D/YYYY` to match the historical format

Each scheduled run:

1. Reads the last row of each CSV to find the most recent snapshot
2. Fetches the latest composition from the official source
3. Compares against the last row
4. If different → appends a new row with today's date
5. Commits the update back to the repo

If nothing changed, the workflow exits cleanly with no commit.

### NSE reference archives (symbol changes + corporate actions)

Two additional files maintain an append-only history of NSE reference data:

```
SYMBOL,COMPANY NAME,SERIES,PURPOSE,FACE VALUE,EX-DATE,RECORD DATE,BOOK CLOSURE START DATE,BOOK CLOSURE END DATE
CGVAK,CG Vak Software & Exports Limited,EQ,Dividend - Re 1 Per Share,10,21-Sep-2026,21-Sep-2026,-,-
...
```

```
Name,Old Symbol,New Symbol,Date of change
360 ONE WAM LIMITED,IIFLWAM,360ONE,23-Jan-2023
...
```

Each scheduled run:

1. Reads the local CSV as-is (existing rows are never modified or removed)
2. Fetches the last 7 days from the NSE endpoint
3. Normalizes both sides semantically (whitespace, case, date format)
4. Appends only rows whose normalized key doesn't already exist locally
5. Commits if anything new was added

The local file is authoritative and append-only. If NSE re-publishes an old record, the sync script will not touch it.

### NSE equity EOD OHLCV

Complete daily bhavcopy for the NSE equity segment, covering **1995-01-02 → present**. Every trading day, every listed symbol — price, volume, delivery quantity, and trade count.

Stored as one Parquet file per completed month, plus a live CSV folder for the current (incomplete) month:

```
NSE/RawDataOHLCV/
├── parquet/
│   ├── 1995-01.parquet
│   ├── 1995-02.parquet
│   ├── ...
│   └── 2026-08.parquet
├── csv/
│   └── 2026-09/                ← current month only
│       ├── 20260901.csv
│       ├── ...
│       └── 20260918.csv
```

**Schema (identical across CSV and Parquet):**

| Column | Type | Description |
|---|---|---|
| `SYMBOL` | categorical | NSE trading symbol |
| `SERIES` | categorical | Series code (`EQ`, `BE`, `GS`, `GB`, etc.) |
| `DATE` | timestamp | Trading date |
| `OPEN` | float32 | Opening price |
| `HIGH` | float32 | Day high |
| `LOW` | float32 | Day low |
| `CLOSE` | float32 | Closing price |
| `LAST` | float32 | Last traded price |
| `PREV_CLOSE` | float32 | Previous close |
| `VOLUME` | int64 | Total traded quantity |
| `TURNOVER` | float64 | Total traded value (rupees) |
| `TRADES` | int64 | Number of trades |
| `ISIN` | string | ISIN code |
| `DELIV_QTY` | int64 | Delivered quantity |
| `DELIV_PER` | float32 | Delivery percentage |
| `SOURCE` | categorical | `old` (1995–2019) or `new` (2019–present) |

Each scheduled run:

1. Scans the filesystem (`parquet/` + `csv/`) to determine what's already collected — the manifest is not used as state
2. Computes the list of missing weekdays from `1995-01-02` up to yesterday
3. Skips dates already classified as permanent market closures (holidays, NSE archive gaps)
4. Downloads each missing date from NSE (NEW endpoint for 2019+, OLD zip archive for 1995–2019)
5. Validates the DATE inside the file matches the filename (rejects NSE's "stale file" responses on holiday URLs)
6. Saves CSVs to `csv/YYYY-MM/YYYYMMDD.csv` (folder auto-created)
7. Any month now fully past → consolidated into `parquet/YYYY-MM.parquet` and CSVs deleted
8. Rebuilds `manifest.csv` from disk, refreshes `failed_events_summary.txt`
9. Commits if anything changed

**Coverage:** ~7,945 of 8,275 expected weekdays. The 430 missing dates are all NSE market closures — documented in `failed_events.csv` with a reason per date.

### US equity EOD OHLCV

Daily OHLCV for every ticker that currently appears in the tracked S&P 500 and Nasdaq-100 universes, covering **2019-01-01 → present**. Data is fetched from **Yahoo Finance** via the `yfinance` Python library.

Unlike the NSE OHLCV dataset, US data is **stored per ticker**, not per month. Each ticker gets its own Parquet file:

```
us_data/raw_ohlcv/
├── parquet/
│   ├── AAPL.parquet
│   ├── MSFT.parquet
│   ├── NVDA.parquet
│   ├── ...
│   └── ZTS.parquet
├── manifest.csv                ← row counts, date ranges, file sizes
├── ticker_aliases.csv          ← old → new symbol mapping (delistings/renames)
├── update_log.csv              ← per-run status (created/updated/rewritten/up_to_date)
└── update_us_ohlcv.py          ← updater script
```

There is **no live CSV folder** for US data — everything is Parquet.

**Schema (identical across every ticker file):**

| Column | Type | Description |
|---|---|---|
| `Ticker` | dictionary (string) | US ticker symbol |
| `Date` | timestamp (ns) | Trading date |
| `Open` | float32 | Opening price |
| `High` | float32 | Day high |
| `Low` | float32 | Day low |
| `Close` | float32 | Closing price |
| `Adj_Close` | float32 | Adjusted closing price (dividends/splits) |
| `Volume` | int64 | Total traded quantity |
| `Split_Ratio` | float32 | Split ratio applied on that date (0 if none) |

Each scheduled run (`update_us_ohlcv.py`):

1. **Load aliases** — reads `ticker_aliases.csv` (generated once from `tirramind.delistings()`) to map old tickers to their successors. This lets the updater follow renames and delistings.
2. **Determine the active universe** — parses the **last row** of the S&P 500 and Nasdaq-100 constituent CSVs, extracts the ticker lists, and validates each symbol against a strict ticker regex.
3. **Diff against existing files** — lists all `*.parquet` files already on disk to find missing tickers.
4. **Update existing tickers** — for each existing Parquet file:
   - If a **new split or dividend** is detected since the file's last date → **full rewrite** from `2019-01-01` to today (so `Adj_Close` and `Split_Ratio` stay correct).
   - Otherwise → **tail fetch** only the dates after the file's last date, concatenate, deduplicate, and overwrite.
   - If the ticker has an alias and the tail is empty, try the alias symbol; if the alias has newer data, force a full rewrite under the original filename.
5. **Fetch missing tickers** — for tickers with no Parquet file yet, do a full history fetch (`2019-01-01 → today`) and write a new Parquet file.
6. **Rebuild `manifest.csv`** — scans every Parquet file on disk and writes `ticker, rows, first_date, last_date, file_size_kb`.
7. **Write `update_log.csv`** — per-ticker status (`created`, `updated`, `rewritten_corp_action`, `up_to_date`, `no_data`).
8. **Commit if anything changed** — only if the Parquet files, manifest, or log actually differ.

**Coverage:** The universe is whatever S&P 500 + Nasdaq-100 contain on the most recent constituent snapshot. New IPOs/joiners get picked up automatically on the next run once they appear in the last row of the constituent CSVs. Tickers that leave both indices are **not deleted** — their Parquet files remain on disk as a historical archive.

## Supported Sources

### S&P 500 and Nasdaq 100

| Dataset | File | Source |
|---|---|---|
| S&P 500 | `SNP500/S&P 500 Historical Components & Changes.csv` | [Wikipedia: Historical components of the S&P 500](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies#Selected_changes_to_the_list_of_S%26P_500_components) |
| Nasdaq 100 | `NDX100/Nasdaq 100 Historical Components & Changes.csv` | [Wikipedia: Historical components of the Nasdaq-100](https://en.wikipedia.org/wiki/Nasdaq-100#Historical_components) |

Both sources list changes with columns for Effective Date, Added Ticker, and Removed Ticker, which is what the scraper relies on.

### NSE indices

| Index | File | Source |
|---|---|---|
| Nifty 50 | `NSE/Nifty_50.csv` | NSE official |
| Nifty 100 | `NSE/Nifty_100.csv` | NSE official |
| Nifty 200 | `NSE/Nifty_200.csv` | NSE official |
| Nifty 500 | `NSE/Nifty_500.csv` | NSE official |
| Nifty Midcap 50 | `NSE/Nifty_Midcap_50.csv` | NSE official |
| Nifty Midcap 100 | `NSE/Nifty_Midcap_100.csv` | NSE official |
| Nifty Midcap 150 | `NSE/Nifty_Midcap_150.csv` | NSE official |
| Nifty MidSmallcap 400 | `NSE/Nifty_MidSmallcap_400.csv` | NSE official |
| Nifty Next 50 | `NSE/Nifty_Next_50.csv` | NSE official |
| Nifty Smallcap 50 | `NSE/Nifty_Smallcap_50.csv` | NSE official |
| Nifty Smallcap 100 | `NSE/Nifty_Smallcap_100.csv` | NSE official |
| Nifty Smallcap 250 | `NSE/Nifty_Smallcap_250.csv` | NSE official |

NSE's endpoint returns a snapshot (current composition only). The script parses the `Symbol` column, compares with the last row, and appends when different.

### NSE reference archives

| Dataset | File | Source |
|---|---|---|
| Symbol changes | `NSE/RawData/symbolchange.csv` | NSE archive |
| Corporate actions | `NSE/RawData/CorporateActions.csv` | NSE API |

Both endpoints are maintained by NSE. The corporate actions endpoint requires a warmed session (homepage visit first) and returns the last N days on request. The symbol change archive is a static CSV, no cookies required.

### NSE equity EOD OHLCV

| Dataset | Location | Source |
|---|---|---|
| NSE equity OHLCV | `NSE/RawDataOHLCV/parquet/` + `NSE/RawDataOHLCV/csv/` | NSE archives |

Two historical endpoints:

- **1995 – 2019-08:** `https://nsearchives.nseindia.com/content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip`
- **2019-08 – present:** `https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv`

The updater tries the newer endpoint first (richer schema — includes delivery quantities), falls back to the older zip archive for dates where the newer one doesn't exist. There is no cookie or session handling required; the archive endpoints are open.

### US equity EOD OHLCV

| Dataset | Location | Source |
|---|---|---|
| US equity OHLCV | `us_data/raw_ohlcv/parquet/<TICKER>.parquet` | Yahoo Finance via `yfinance` |

- **Provider:** Yahoo Finance (`https://finance.yahoo.com`)
- **Python client:** `yfinance` (version pinned in `requirements.txt`)
- **Authentication:** none required — `yfinance` uses Yahoo's public quote endpoints
- **Coverage start:** `2019-01-01` (hard-coded in `update_us_ohlcv.py` as `START_DATE`)
- **Universe:** current S&P 500 + Nasdaq-100 members (from the last row of the constituent CSVs)
- **Alias handling:** `tirramind` is used **once** to generate `ticker_aliases.csv` (mapping delisted/re-named tickers to successors). After that file exists, the updater relies on it and does not call `tirramind` again.

## Repository Structure

```
Auto-Index-Constituents-Tracker/
│
├── .github/workflows/
│   ├── snp500.yml                            # S&P 500 daily automation
│   ├── ndx100.yml                            # Nasdaq 100 daily automation
│   ├── nse.yml                               # NSE index daily automation
│   ├── symbolchange.yaml                     # NSE symbol change daily automation
│   ├── corporate_actions.yaml                # NSE corporate actions daily automation
│   ├── nse_ohlcv.yml                         # NSE equity OHLCV daily automation
│   └── US_ohlcv.yml                          # US equity OHLCV daily automation
│
├── SNP500/
│   ├── update_snp500.py
│   └── S&P 500 Historical Components & Changes.csv
│
├── NDX100/
│   ├── update_ndx100.py
│   └── Nasdaq 100 Historical Components & Changes.csv
│
├── NSE/
│   ├── update_nse.py
│   ├── Nifty_50.csv
│   ├── Nifty_100.csv
│   ├── ... (12 NSE index files)
│   │
│   ├── RawData/
│   │   ├── update_symbolchange.py
│   │   ├── symbolchange.csv
│   │   ├── update_corporate_actions.py
│   │   └── CorporateActions.csv
│   │
│   └── RawDataOHLCV/
│       ├── update_ohlcv.py
│       ├── README.md
│       ├── manifest.csv
│       ├── failed_events.csv
│       ├── failed_events_summary.txt
│       ├── parquet/
│       │   ├── 1995-01.parquet
│       │   ├── ...
│       │   └── 2026-08.parquet
│       └── csv/
│           └── 2026-09/
│               └── YYYYMMDD.csv
│
├── us_data/
│   └── raw_ohlcv/
│       ├── update_us_ohlcv.py
│       ├── manifest.csv
│       ├── ticker_aliases.csv
│       ├── update_log.csv
│       └── parquet/
│           ├── AAPL.parquet
│           ├── MSFT.parquet
│           ├── NVDA.parquet
│           ├── ...
│           └── ZTS.parquet
│
├── requirements.txt
└── README.md
```

Each source is self-contained: own folder, own script, own workflow. Independent schedules, independent failure domains. If one source breaks, the others continue running.

## How It Works

### S&P 500 and Nasdaq 100

The scripts (`SNP500/update_snp500.py`, `NDX100/update_ndx100.py`):

1. Load the CSV and determine the last recorded date
2. Parse the latest ticker list from the last row
3. Scrape the corresponding Wikipedia change-log table
4. Filter to changes strictly newer than the last date
5. Apply each change in chronological order — removing tickers that left, adding tickers that joined
6. Append a new row per change date
7. Overwrite the file with normalized date formatting

Wikipedia provides exact effective dates, so rows reflect when changes actually took effect.

### NSE indices

The script (`NSE/update_nse.py`):

1. Download the current snapshot from `niftyindices.com` for each index
2. Extract the `Symbol` column, sort alphabetically
3. Compare against the last row's tickers
4. If different → append a new row with today's date
5. If identical → no change, no commit

NSE's endpoint does not provide an effective date — only the current composition. So detection date is used, which may trail the actual effective date by 1–3 days.

### NSE symbol changes

The script (`NSE/RawData/update_symbolchange.py`):

1. Fetch the static CSV from `nsearchives.nseindia.com`
2. Validate structurally (four comma-separated fields per row; remote has no header)
3. Build a normalized key per remote row: `(Name, Old Symbol, New Symbol, Date)` with whitespace collapse, case folding, ISO date conversion, and leading-zero stripping on purely numeric symbols
4. Skip any remote row whose key already exists locally
5. Append remaining rows, canonicalizing dates to `DD-Mon-YYYY`
6. Sort by `Name` then `Date` for stability

### NSE corporate actions

The script (`NSE/RawData/update_corporate_actions.py`):

1. Warm cookies by visiting `nseindia.com`
2. Fetch the last 7 days from the corporate actions API
3. Build a normalized key per row across all nine columns
4. Skip any remote row whose key already exists locally
5. Append remaining rows in `EX-DATE`-ascending order

The 7-day rolling window (not just today) catches late NSE posts, missed runs, and post-announcement edits. Re-fetching overlaps is harmless — the dedupe layer filters everything that already exists.

### NSE equity OHLCV

The script (`NSE/RawDataOHLCV/update_ohlcv.py`):

1. **Scan disk** — walks `parquet/` and `csv/` to build the set of dates already collected. The manifest is not trusted as state; it's regenerated from disk each run.
2. **Compute missing** — enumerates every weekday from `1995-01-02` to yesterday, subtracts dates on disk, subtracts dates in `failed_events.csv` marked as permanent (holidays, NSE archive gaps).
3. **Download each missing date** — tries the NEW endpoint first (2019+), falls back to the OLD zip endpoint (1995–2019).
4. **Validate** — rejects files where the internal `DATE` column doesn't match the filename. This catches NSE's habit of serving the previous trading day's data on holiday URLs.
5. **Save CSV** — to `csv/YYYY-MM/YYYYMMDD.csv`. The month folder is auto-created if new.
6. **Consolidate** — for any `csv/YYYY-MM/` folder where the month is now in the past, reads all CSVs, casts to the final schema (float32 prices, Int64 volumes, categorical strings, sorted by SYMBOL then DATE), writes a single zstd-9 Parquet file to `parquet/YYYY-MM.parquet`, verifies row count on readback, then deletes the CSVs.
7. **Rebuild manifest** — from disk, not memory.
8. **Write summary** — regenerates `failed_events_summary.txt` with coverage-by-year.

Failure classes are logged in `failed_events.csv`: `FIXED_HOLIDAY` (Republic Day, Independence Day, etc.), `HOLIDAY_NON_FIXED` (Diwali, Holi, Eid, etc.), `PERMANENT_GAP` (NSE returns empty), `NETWORK_ERROR` and `PARSE_ERROR` (retried next run).

The updater runs every day including weekends. Weekend runs are effectively no-ops (the expected weekday list naturally skips them), but the Saturday run ensures Friday's data is collected the morning after, rather than waiting until Monday.

### US equity OHLCV

The script (`us_data/raw_ohlcv/update_us_ohlcv.py`):

1. **Load aliases** — reads `ticker_aliases.csv`, a one-time-generated mapping from `tirramind.delistings()` that links old tickers to their successors. If the file doesn't exist yet, it is generated first.
2. **Build the active universe** — reads the **last row** of `SNP500/S&P 500 Historical Components & Changes.csv` and `NDX100/Nasdaq 100 Historical Components & Changes.csv`, splits the ticker strings, and validates each against `^(?=.*[A-Z])[A-Z0-9\.\-]{1,8}$`.
3. **Diff against disk** — lists every `*.parquet` in `us_data/raw_ohlcv/parquet/` to find which universe tickers are missing.
4. **Update existing tickers** — for each existing file:
   - Checks for **new splits** (`ticker.splits`) or **new dividends** (`ticker.dividends`) after the file's last date.
   - If either exists → **full rewrite** from `2019-01-01` to today (keeps `Adj_Close` and `Split_Ratio` accurate).
   - Otherwise → **tail fetch** only `last_date + 1 day → today`, concatenate, deduplicate on `Date`, and overwrite.
   - If the ticker has an alias and the tail is empty, retries with the alias symbol; if the alias has newer data, forces a full rewrite under the original filename.
5. **Fetch missing tickers** — for every universe ticker with no Parquet file, does a full history fetch from `2019-01-01` to today and writes a new file.
6. **Rebuild manifest** — scans every Parquet file and writes `ticker, rows, first_date, last_date, file_size_kb` to `manifest.csv`.
7. **Write update log** — records `symbol, status, rows, new_rows` per ticker in `update_log.csv`.
8. **Commit** — only if the Parquet files, `manifest.csv`, or `update_log.csv` actually changed.

Random sleeps are inserted every 50 tickers (2–4 seconds) and after each new ticker (0.5–1.5 seconds) to stay friendly to Yahoo Finance’s rate limits.

### Idempotency and append-only guarantee

All scripts are idempotent. Re-running with no source-side changes produces no output and no commit.

The two NSE reference archives are append-only: existing rows are never modified, deleted, or reordered. If NSE re-publishes an old record, the sync script silently skips it. If a row you have locally differs slightly from what NSE now shows (e.g. a typo fix), both versions persist — the historical record is preserved.

The OHLCV collections (NSE and US) are also idempotent, but not append-only in the same sense — a given date's data can be safely re-downloaded and overwritten. For NSE, the filesystem is the source of truth; deleting a file causes the next run to re-fetch it. For US, the per-ticker Parquet file is the source of truth; a full rewrite simply replaces it.

## Automated Schedules

All **seven** workflows run in the early morning IST, staggered 10 minutes apart so their commits never race on push:

| Workflow | Cron (UTC) | IST equivalent | Source |
|---|---|---|---|
| `snp500.yml` | `30 23 * * *` | 5:00 AM | Wikipedia |
| `ndx100.yml` | `40 23 * * *` | 5:10 AM | Wikipedia |
| `nse.yml` | `50 23 * * *` | 5:20 AM | NSE Indices |
| `corporate_actions.yaml` | `0 0 * * *` | 5:30 AM | NSE API |
| `symbolchange.yaml` | `10 0 * * *` | 5:40 AM | NSE archive |
| `nse_ohlcv.yml` | `20 0 * * *` | 5:50 AM | NSE archive |
| `US_ohlcv.yml` | `05 3 * * *` | 8:35 AM | Yahoo Finance (`yfinance`) |

- GitHub Actions schedules are always in UTC and may be delayed by 5–30 minutes during peak load
- The 10-minute gaps mean each workflow finishes and pushes before the next one starts — no commit races, no rebase needed
- Manual runs can be triggered anytime from the Actions tab → select the workflow → Run workflow
- `nse_ohlcv.yml` and `US_ohlcv.yml` run seven days a week. On weekends the OHLCV scripts exit quickly because there is nothing new to fetch

## Setup

### Prerequisites

- A GitHub account
- A free or paid GitHub Actions plan (free is sufficient — this repo uses roughly <!-- FILL IN: recalculated minutes --> minutes of Actions time per month across all seven workflows)

### GitHub Setup

1. Ensure all workflow files exist under `.github/workflows/` on the default branch (`main`)
2. Go to **Settings → Actions → General → Workflow permissions**
3. Select **"Read and write permissions"**
4. Click **Save**

Without this step, the workflows will run but fail to push commits back to the repository.

### Local Development

Clone the repo:

```bash
git clone https://github.com/YOUR-USERNAME/Auto-Index-Constituents-Tracker.git
cd Auto-Index-Constituents-Tracker
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run any updater:

```bash
python SNP500/update_snp500.py
python NDX100/update_ndx100.py
python NSE/update_nse.py
python NSE/RawData/update_symbolchange.py
python NSE/RawData/update_corporate_actions.py
python NSE/RawDataOHLCV/update_ohlcv.py
python us_data/raw_ohlcv/update_us_ohlcv.py
```

Each script prints a short summary — local row count, remote row count, and whether anything was added.

The OHLCV updaters additionally support:

```bash
python NSE/RawDataOHLCV/update_ohlcv.py --dry-run          # show plan, no downloads
python NSE/RawDataOHLCV/update_ohlcv.py --no-consolidate   # skip Parquet conversion
python NSE/RawDataOHLCV/update_ohlcv.py --date 2026-09-18  # single date

python us_data/raw_ohlcv/update_us_ohlcv.py --missing-only  # only fetch tickers with no Parquet file
```

## Reading the OHLCV data

### NSE

```python
import pandas as pd

# One month
df = pd.read_parquet("NSE/RawDataOHLCV/parquet/2020-03.parquet")

# One year
import glob
dfs = [pd.read_parquet(f) for f in glob.glob("NSE/RawDataOHLCV/parquet/2020-*.parquet")]
df_2020 = pd.concat(dfs, ignore_index=True)

# One stock's full history
import glob, pandas as pd
dfs = []
for f in sorted(glob.glob("NSE/RawDataOHLCV/parquet/*.parquet")):
    d = pd.read_parquet(f, columns=["SYMBOL", "DATE", "CLOSE"])
    dfs.append(d[d["SYMBOL"] == "RELIANCE"])
reliance = pd.concat(dfs).sort_values("DATE")
```

The current (incomplete) month lives at `NSE/RawDataOHLCV/csv/YYYY-MM/` as one CSV per trading day. It is browsable directly on GitHub.

### US

```python
import pandas as pd, glob

# One ticker's full history
aapl = pd.read_parquet("us_data/raw_ohlcv/parquet/AAPL.parquet")

# Every ticker on a given date (useful for cross-sectional work)
import glob
frames = []
for f in glob.glob("us_data/raw_ohlcv/parquet/*.parquet"):
    d = pd.read_parquet(f, columns=["Ticker", "Date", "Close"])
    frames.append(d[d["Date"] == "2025-06-30"])
cross_section = pd.concat(frames).set_index("Ticker")
```

There is **no CSV layer** for US data — all history lives in the per-ticker Parquet files. The `manifest.csv` gives you a quick index of every ticker, its row count, and its date range.

## Adding a New Source

### For S&P 500 / Nasdaq 100 style (Wikipedia-sourced)

1. Copy an existing script (e.g., `SNP500/update_snp500.py`)
2. Edit the `INDEX` dict at the top — update `name`, `file`, and `url`
3. Drop the corresponding CSV into the same folder
4. Add a matching workflow under `.github/workflows/`
5. Commit and push

### For NSE index style (official snapshot)

1. Open `NSE/update_nse.py`
2. Add an entry to the `INDICES` dict — `"Nifty_XXX.csv": "https://.../ind_niftyxxxlist.csv"`
3. Drop a starter CSV (with header `date,tickers` and at least one row) into `NSE/`
4. Commit and push

The next scheduled run picks it up automatically.

### For NSE reference archive style (append-only)

1. Copy `NSE/RawData/update_symbolchange.py` as a template
2. Replace `NSE_CSV_URL` / `API` / `COLS` with the new source's values
3. Drop a starter CSV into `NSE/RawData/`
4. Add a workflow that targets only the new CSV (`git add` must name the file explicitly, not the folder — otherwise the two workflows can stage each other's changes)
5. Commit and push

**Note:** The Wikipedia scraper tolerates minor variations in table headers because it locates columns by keyword. NSE requires the response CSV to have a `Symbol` column.

### For OHLCV-style (bulk historical + daily incremental)

1. Copy `NSE/RawDataOHLCV/update_ohlcv.py` or `us_data/raw_ohlcv/update_us_ohlcv.py` as a template
2. Adjust `COLLECTION_START` / `START_DATE`, the URL builders, and the failure-classification rules for the new source
3. Add a matching workflow under `.github/workflows/`
4. Commit and push

## Manual Trigger

To run any workflow immediately:

1. Go to the **Actions** tab
2. Click the workflow name in the left sidebar
3. Click **Run workflow → Run workflow**

Each run takes roughly 30–90 seconds depending on the source. The OHLCV updaters’ runtime varies with how many dates/tickers are missing — usually 1–2 seconds on a normal day, up to a few minutes when catching up.

## Credits & Data Sources

### Inspiration & Original S&P 500 Dataset

The S&P 500 history in this repository originated from [fja05680/sp500](https://github.com/fja05680/sp500), a widely-used open-source project that maintains current and historical S&P 500 component lists since 1996. That repository provided:

- The original `S&P 500 Historical Components & Changes.csv` file (1996–2019)
- The methodology for merging historical data with Wikipedia's change log
- The two-column `date,tickers` CSV format used throughout this repo

The `fja05680/sp500` project is licensed under the MIT License. Anyone using this repository's S&P 500 data should review that project's license terms.

### Original Book Source (S&P 500, 1996–2019)

The original S&P 500 constituent history from 1996 to 2019 was distributed as a downloadable file accompanying the book **"Trading Evolved"** by Andreas F. Clenow.

Clenow is a professional quantitative trader and author. The data in *Trading Evolved* was sourced from **Norgate Data**, which is one of the few providers that properly handles delisted and renamed securities — essential for survivorship-bias-free backtesting.

- Book: *Trading Evolved* by Andreas F. Clenow
- Data provider referenced: Norgate Data

All credit for the original 1996–2019 S&P 500 constituent history belongs to Andreas Clenow and Norgate Data.

### Wikipedia

Both US indices rely on Wikipedia's community-maintained change logs:

- S&P 500: [Historical components of the S&P 500](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies#Selected_changes_to_the_list_of_S%26P_500_components)
- Nasdaq 100: [Historical components of the Nasdaq-100](https://en.wikipedia.org/wiki/Nasdaq-100#Historical_components)

Wikipedia content is available under the [Creative Commons Attribution-ShareAlike 4.0 International License](https://creativecommons.org/licenses/by-sa/4.0/).

**Important caveat:** Wikipedia's change logs are not exhaustive. They show selected changes and are sometimes incomplete, delayed, or contain errors. They are a helpful starting point but should not be treated as an authoritative source for regulatory or financial decisions.

### Nasdaq 100 Historical Data

The initial Nasdaq 100 constituent history in this repository was compiled from the Wikipedia historical components page linked above. Unlike the S&P 500 data, it does not derive from a commercial dataset. Users should be aware that:

- Coverage begins in February 2007
- Wikipedia's Nasdaq 100 change log may be less complete than the S&P 500 one
- The data has not been independently verified against a commercial source

### NSE Data

NSE constituent snapshots are sourced directly from NSE Indices Limited (a subsidiary of the National Stock Exchange of India):

- Endpoint pattern: `https://www.niftyindices.com/IndexConstituent/ind_<index>list.csv`
- Each snapshot lists the current constituents with columns: `Company Name`, `Industry`, `Symbol`, `Series`, `ISIN Code`
- NSE updates indices on a periodic review basis (semi-annual for broad-market, quarterly for some strategy indices, ad-hoc for corporate actions)

NSE symbol changes are fetched from the archive endpoint:

- `https://nsearchives.nseindia.com/content/equities/symbolchange.csv`
- Columns: `Name`, `Old Symbol`, `New Symbol`, `Date of change` (no header in the remote file — validated structurally)

NSE corporate actions are fetched from the corporate filings API:

- `https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date=...&to_date=...&csv=true`
- Columns: `SYMBOL`, `COMPANY NAME`, `SERIES`, `PURPOSE`, `FACE VALUE`, `EX-DATE`, `RECORD DATE`, `BOOK CLOSURE START DATE`, `BOOK CLOSURE END DATE`
- Requires a warmed session (visit `nseindia.com` first to establish cookies) and browser-like headers

NSE equity OHLCV data is sourced from two NSE archive endpoints:

- `https://nsearchives.nseindia.com/content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip` (1995–2019)
- `https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv` (2019–present)

Both are open, unauthenticated, and use standard HTTP. The OHLCV updater does not require a warmed session.

NSE Indices Limited is the owner of all NSE index and OHLCV data. Redistribution is subject to NSE's terms of use.

### US Equity OHLCV Data

The US OHLCV dataset is sourced from **Yahoo Finance** through the [`yfinance`](https://github.com/ranaroussi/yfinance) Python library.

- **Provider:** Yahoo Finance (`https://finance.yahoo.com`)
- **Client library:** `yfinance` (MIT License)
- **Authentication:** none — public quote endpoints
- **Terms of use:** Yahoo Finance’s [Terms of Service](https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.html) and [API Terms](https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.html) apply. Yahoo does not provide an official public API for bulk historical data; `yfinance` relies on Yahoo’s website endpoints. Users are responsible for ensuring their usage complies with Yahoo’s terms.
- **Adjustment:** `Adj_Close` is populated from Yahoo’s “Adj Close” column (split- and dividend-adjusted). `Close` is the raw closing price. `Split_Ratio` is non-zero only on dates where Yahoo reports a split.
- **Delisted / renamed tickers:** `ticker_aliases.csv` (generated once from `tirramind.delistings()`) maps old tickers to successors. If Yahoo has no data under the old symbol but the alias does, the updater rewrites the file under the old symbol with the alias’s data. Tickers that leave the S&P 500 / Nasdaq-100 universe are **not deleted** — their last-known Parquet file remains on disk.
- **Universe bias:** The ticker universe is the **current** S&P 500 + Nasdaq-100 membership. Historical members that are no longer in either index are not automatically fetched unless they are reachable via an alias. This means the US OHLCV dataset is **not survivorship-bias-free** in the way the constituent CSVs are — it tracks the current universe forward from 2019.

### How the NSE Historical Data Was Built

Unlike the S&P 500 dataset (which traces back to a single commercial source), the NSE historical files were assembled manually from two sources:

1. Historical snapshots collected over time — for dates where we had a saved copy of the constituents
2. NSE's official press releases — for additions and removals announced via NSE Indices Limited circulars

This means the NSE files are best-effort reconstructions, not a certified historical record. As a result:

- Some change dates may be missing — if no press release or snapshot was available
- Some change dates may be approximate — detection date is used where an exact effective date wasn't published
- Corporate actions (mergers, demergers, name changes, ticker symbol changes) may appear as adds/removes when they are really the same company continuing under a new identity
- NSE occasionally revises announced changes, and those revisions may not be reflected if they occurred after the initial press release

The `CorporateActions.csv` and `symbolchange.csv` files were built from a large bulk download of NSE's own archives, then extended daily by the sync scripts. The bulk download captured all history NSE publicly exposes; the daily syncs append new entries as NSE publishes them.

The OHLCV dataset was bulk-downloaded once (1995-01-02 through the collection date), validated against NSE's own archive, and has been incrementally extended by the daily workflow since. Every date's internal `DATE` column is checked against its filename to reject NSE's occasional "stale file on holiday URL" behavior.

Where accuracy was possible, it was prioritized. Where it wasn't, the file reflects the best available information at the time of collection.

For any research or backtesting that depends on precise constituent or price history, verify the NSE files against primary sources — NSE Indices Limited's circular archive and the historical index factsheets. Treat this dataset as a strong starting point, not a ground truth.

## Disclaimer

This repository is provided for research and educational purposes only.

- The constituent data is assembled from community-maintained and official sources, and may contain errors, omissions, or inconsistencies
- Wikipedia's change logs are not guaranteed to be complete, accurate, or timely
- The S&P 500 data traces back to a commercial data provider (Norgate Data) via a book download; redistribution or commercial use may be subject to additional terms
- NSE constituent data is owned by NSE Indices Limited and subject to their terms of use
- NSE historical constituent data is a best-effort reconstruction from archived snapshots and press releases — it may contain gaps, approximate dates, or corporate actions miscategorized as changes
- The symbol change and corporate action archives are append-only and preserved as-is. Errors present in NSE's published data will propagate through to these files
- The NSE OHLCV data is sourced from NSE's public archives. Precision on prices is limited to float32 (~7 significant digits) — sufficient for NSE's 2-decimal price convention, but not for high-precision financial computation
- The US OHLCV data is sourced from Yahoo Finance via `yfinance` and is subject to Yahoo's terms of service. Prices are float32; `Adj_Close` is Yahoo's adjusted close, which may change retroactively if Yahoo revises its adjustment factors. The universe is the current S&P 500 + Nasdaq-100 membership, so it is **not survivorship-bias-free** for dates before a ticker joined the index
- Nothing here constitutes financial advice

If you are using this data for quantitative backtesting, be aware of the risks of survivorship bias and look-ahead bias. The constituent CSVs are specifically designed to help mitigate survivorship bias by preserving the full historical membership list — but only if the data is correct. The US OHLCV dataset, by contrast, tracks the *current* universe forward from 2019 and therefore reflects survivorship bias for earlier dates.

Always verify against an authoritative source before making financial decisions.

## License

The code in this repository (all `update_*.py` files, workflow files) is available under the MIT License.

The data in this repository is subject to the terms of its upstream sources:

- S&P 500 historical data (1996–2019): originally from *Trading Evolved* by Andreas Clenow / Norgate Data
- S&P 500 change data (2019–present) and format: derived from [fja05680/sp500](https://github.com/fja05680/sp500) (MIT License)
- Wikipedia-derived change data: CC BY-SA 4.0
- Nasdaq 100 historical data: Wikipedia, CC BY-SA 4.0
- NSE index, symbol change, corporate actions, and OHLCV data: © NSE Indices Limited
- US equity OHLCV data: Yahoo Finance; `yfinance` client library is MIT-licensed, but the underlying data is subject to Yahoo's Terms of Service

Users are responsible for complying with all applicable upstream licenses.

## Acknowledgements

- **Andreas F. Clenow** — author of *Trading Evolved*, original compiler of the 1996–2019 S&P 500 constituent history
- **Norgate Data** — the underlying data provider for the original S&P 500 history
- **fja05680** — creator of [fja05680/sp500](https://github.com/fja05680/sp500), which provided the foundation, format, and methodology for this project
- **Wikipedia contributors** — for maintaining the historical components pages that make ongoing automation possible
- **NSE Indices Limited** — for publishing official constituent lists, symbol change archives, corporate action announcements, and EOD bhavcopy archives
- **Yahoo Finance** and **`yfinance` contributors** — for making daily US equity OHLCV accessible without a commercial data contract

## Related Projects

- [fja05680/sp500](https://github.com/fja05680/sp500) — the original S&P 500 constituent tracker that inspired this project
- [yfiua/index-constituents](https://github.com/yfiua/index-constituents) — another open-source index constituent dataset (Apache-2.0)
- [hanshof/sp500_constituents](https://github.com/hanshof/sp500_constituents) — S&P 500 constituents from 1996 to present
