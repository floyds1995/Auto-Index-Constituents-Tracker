# Auto-Index-Constituents-Tracker

Automated daily tracker for historical index constituents. Currently supports **S&P 500**, **Nasdaq 100**, and **13 NSE indices** (Nifty 50 through Nifty Smallcap 250).

Each index is stored as a single CSV containing the full membership of that index on every date a change occurred. GitHub Actions runs on a schedule, fetches the latest composition from the official source, and appends a new row only when something actually changed.

No servers. No cron jobs. No external services — just GitHub Actions, Wikipedia, and NSE's official endpoints.

---

## What It Does

Every tracked index is stored in this format:

```
date,tickers
1996-01-02,"AAL,AAMRQ,AAPL,ABI,..."
1996-01-03,"AAL,AAMRQ,AAPL,ABI,..."
...
2026-08-18,"AAPL,ABNB,ADBE,..."
```

- **One row per date the composition changed** — not every trading day
- **`tickers`** is the full, sorted, comma-separated list of constituents on that date
- The last row always represents the current composition
- S&P 500 and Nasdaq 100 use ISO dates (`YYYY-MM-DD`); NSE files use `M/D/YYYY` to match the historical format

Each scheduled run:

1. Reads the last row of each CSV to find the most recent snapshot
2. Fetches the latest composition from the official source
3. Compares against the last row
4. If different → appends a new row with today's date
5. Commits the update back to the repo

**If nothing changed, the workflow exits cleanly with no commit.**

---

## Supported Indices

### S&P 500 and Nasdaq 100

| Index | Data File | Source |
|---|---|---|
| S&P 500 | `SNP500/S&P 500 Historical Components & Changes.csv` | [Wikipedia: Historical components of the S&P 500](https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500) |
| Nasdaq 100 | `NDX100/Nasdaq 100 Historical Components & Changes.csv` | [Wikipedia: Historical components of the Nasdaq-100](https://en.wikipedia.org/wiki/Historical_components_of_the_Nasdaq-100) |

Both sources list changes with columns for **Effective Date**, **Added Ticker**, and **Removed Ticker**, which is what the scraper relies on.

### NSE Indices

| Index | Data File | Source |
|---|---|---|
| Nifty 50 | `NSE/Nifty_50.csv` | [NSE official](https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv) |
| Nifty 100 | `NSE/Nifty_100.csv` | [NSE official](https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv) |
| Nifty 200 | `NSE/Nifty_200.csv` | [NSE official](https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv) |
| Nifty 500 | `NSE/Nifty_500.csv` | [NSE official](https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv) |
| Nifty Midcap 50 | `NSE/Nifty_Midcap_50.csv` | [NSE official](https://www.niftyindices.com/IndexConstituent/ind_niftymidcap50list.csv) |
| Nifty Midcap 100 | `NSE/Nifty_Midcap_100.csv` | [NSE official](https://www.niftyindices.com/IndexConstituent/ind_niftymidcap100list.csv) |
| Nifty Midcap 150 | `NSE/Nifty_Midcap_150.csv` | [NSE official](https://www.niftyindices.com/IndexConstituent/ind_niftymidcap150list.csv) |
| Nifty MidSmallcap 400 | `NSE/Nifty_MidSmallcap_400.csv` | [NSE official](https://www.niftyindices.com/IndexConstituent/ind_niftymidsmallcap400list.csv) |
| Nifty Next 50 | `NSE/Nifty_Next_50.csv` | [NSE official](https://www.niftyindices.com/IndexConstituent/ind_niftynext50list.csv) |
| Nifty Smallcap 50 | `NSE/Nifty_Smallcap_50.csv` | [NSE official](https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap50list.csv) |
| Nifty Smallcap 100 | `NSE/Nifty_Smallcap_100.csv` | [NSE official](https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap100list.csv) |
| Nifty Smallcap 250 | `NSE/Nifty_Smallcap_250.csv` | [NSE official](https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap250list.csv) |

NSE's endpoint returns a snapshot (current composition only). The script parses the `Symbol` column, compares with the last row, and appends when different.

---

## Repository Structure

```
Auto-Index-Constituents-Tracker/
│
├── .github/workflows/
│   ├── snp500.yml                            # S&P 500 daily automation
│   ├── ndx100.yml                            # Nasdaq 100 daily automation
│   └── nse.yml                               # NSE daily automation
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
│   └── ... (12 NSE index files)
│
├── requirements.txt
└── README.md
```

Each source is self-contained: own folder, own script, own workflow. Independent schedules, independent failure domains. If one source breaks, the others continue running.

---

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

Wikipedia provides **exact effective dates**, so rows reflect when changes actually took effect.

### NSE Indices

The script (`NSE/update_nse.py`):

1. Download the current snapshot from `niftyindices.com` for each index
2. Extract the `Symbol` column, sort alphabetically
3. Compare against the last row's tickers
4. If different → append a new row with today's date
5. If identical → no change, no commit

NSE's endpoint does not provide an effective date — only the current composition. So detection date is used, which may trail the actual effective date by 1–3 days.

### Idempotency

All scripts are idempotent. Re-running with no source-side changes produces no output and no commit. Safe on any schedule.

---

## Automated Schedules

All three workflows run in the early morning IST, staggered 10 minutes apart so their commits never race on push:

| Workflow | Cron (UTC) | IST equivalent | Source |
|---|---|---|---|
| `snp500.yml` | `30 23 * * *` | 5:00 AM | Wikipedia |
| `ndx100.yml` | `40 23 * * *` | 5:10 AM | Wikipedia |
| `nse.yml` | `50 23 * * *` | 5:20 AM | NSE |

- GitHub Actions schedules are always in UTC and may be delayed by 5–30 minutes during peak load
- The 10-minute gaps mean each workflow finishes and pushes before the next one starts — no commit races, no rebase needed
- **Manual runs** can be triggered anytime from the **Actions** tab → select the workflow → **Run workflow**

---

## Setup

### Prerequisites

- A GitHub account
- A free or paid GitHub Actions plan (free is sufficient — this repo uses roughly 45 minutes of Actions time per month across all three workflows)

### GitHub Setup

1. Ensure all workflow files exist under `.github/workflows/` on the default branch (`main`)
2. Go to **Settings → Actions → General → Workflow permissions**
3. Select **"Read and write permissions"**
4. Click **Save**

Without this step, the workflows will run but fail to push commits back to the repository.

### Local Development

1. Clone the repo:
   ```bash
   git clone https://github.com/YOUR-USERNAME/Auto-Index-Constituents-Tracker.git
   cd Auto-Index-Constituents-Tracker
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run any updater:
   ```bash
   python SNP500/update_snp500.py
   python NDX100/update_ndx100.py
   python NSE/update_nse.py
   ```

---

## Adding a New Index

### For S&P 500 / Nasdaq 100 style (Wikipedia-sourced)

1. Copy an existing script (e.g., `SNP500/update_snp500.py`)
2. Edit the `INDEX` dict at the top — update `name`, `file`, and `url`
3. Drop the corresponding CSV into the same folder
4. Add a matching workflow under `.github/workflows/`
5. Commit and push

### For NSE style (official snapshot)

1. Open `NSE/update_nse.py`
2. Add an entry to the `INDICES` dict — `"Nifty_XXX.csv": "https://.../ind_niftyxxxlist.csv"`
3. Drop a starter CSV (with header `date\ttickers` and at least one row) into `NSE/`
4. Commit and push

The next scheduled run picks it up automatically.

**Note:** The Wikipedia scraper tolerates minor variations in table headers because it locates columns by keyword. NSE requires the response CSV to have a `Symbol` column.

---

## Manual Trigger

To run any workflow immediately:

1. Go to the **Actions** tab
2. Click the workflow name in the left sidebar (`Update S&P 500 Constituents`, `Update Nasdaq 100 Constituents`, or `Update NSE Constituents`)
3. Click **Run workflow** → **Run workflow**

Each run takes roughly 30–60 seconds.

---

## Credits & Data Sources

### Inspiration & Original S&P 500 Dataset

The S&P 500 history in this repository originated from **[fja05680/sp500](https://github.com/fja05680/sp500)**, a widely-used open-source project that maintains current and historical S&P 500 component lists since 1996. That repository provided:

- The original `S&P 500 Historical Components & Changes.csv` file (1996–2019)
- The methodology for merging historical data with Wikipedia's change log
- The two-column `date,tickers` CSV format used throughout this repo

The `fja05680/sp500` project is licensed under the **MIT License**. Anyone using this repository's S&P 500 data should review that project's license terms.

### Original Book Source (S&P 500, 1996–2019)

The original S&P 500 constituent history from 1996 to 2019 was distributed as a downloadable file accompanying the book **"Trading Evolved"** by **Andreas F. Clenow**.

Clenow is a professional quantitative trader and author. The data in *Trading Evolved* was sourced from **Norgate Data**, which is one of the few providers that properly handles delisted and renamed securities — essential for survivorship-bias-free backtesting.

- Book: *Trading Evolved* by Andreas F. Clenow
- Data provider referenced: [Norgate Data](https://norgatedata.com/)

All credit for the original 1996–2019 S&P 500 constituent history belongs to Andreas Clenow and Norgate Data.

### Wikipedia

Both US indices rely on Wikipedia's community-maintained change logs:

- **S&P 500:** [Historical components of the S&P 500](https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500)
- **Nasdaq 100:** [Historical components of the Nasdaq-100](https://en.wikipedia.org/wiki/Historical_components_of_the_Nasdaq-100)

Wikipedia content is available under the [Creative Commons Attribution-ShareAlike 4.0 International License](https://creativecommons.org/licenses/by-sa/4.0/).

**Important caveat:** Wikipedia's change logs are **not exhaustive**. They show *selected* changes and are sometimes incomplete, delayed, or contain errors. They are a helpful starting point but should not be treated as an authoritative source for regulatory or financial decisions.

### Nasdaq 100 Historical Data

The initial Nasdaq 100 constituent history in this repository was compiled from the Wikipedia historical components page linked above. Unlike the S&P 500 data, it does not derive from a commercial dataset. Users should be aware that:

- Coverage begins in **February 2007**
- Wikipedia's Nasdaq 100 change log may be less complete than the S&P 500 one
- The data has not been independently verified against a commercial source

### NSE Data

NSE constituent snapshots are sourced directly from **NSE Indices Limited** (a subsidiary of the National Stock Exchange of India):

- Endpoint pattern: `https://www.niftyindices.com/IndexConstituent/ind_<index>list.csv`
- Each snapshot lists the current constituents with columns: `Company Name`, `Industry`, `Symbol`, `Series`, `ISIN Code`
- NSE updates indices on a periodic review basis (semi-annual for broad-market, quarterly for some strategy indices, ad-hoc for corporate actions)

NSE Indices Limited is the owner of all NSE index data. Redistribution is subject to NSE's terms of use.

---

## Disclaimer

This repository is provided **for research and educational purposes only**.

- The constituent data is assembled from community-maintained and official sources, and **may contain errors, omissions, or inconsistencies**
- Wikipedia's change logs are not guaranteed to be complete, accurate, or timely
- The S&P 500 data traces back to a commercial data provider (Norgate Data) via a book download; redistribution or commercial use may be subject to additional terms
- NSE constituent data is owned by NSE Indices Limited and subject to their terms of use
- Nothing here constitutes financial advice

If you are using this data for quantitative backtesting, be aware of the risks of **survivorship bias** and **look-ahead bias**. This dataset is specifically designed to help mitigate survivorship bias by preserving the full historical membership list — but only if the data is correct.

**Always verify against an authoritative source before making financial decisions.**

---

## License

The **code** in this repository (all `update_*.py` files, workflow files) is available under the MIT License.

The **data** in this repository is subject to the terms of its upstream sources:

- S&P 500 historical data (1996–2019): originally from *Trading Evolved* by Andreas Clenow / Norgate Data
- S&P 500 change data (2019–present) and format: derived from [fja05680/sp500](https://github.com/fja05680/sp500) (MIT License)
- Wikipedia-derived change data: CC BY-SA 4.0
- Nasdaq 100 historical data: Wikipedia, CC BY-SA 4.0
- NSE constituent data: © NSE Indices Limited

Users are responsible for complying with all applicable upstream licenses.

---

## Acknowledgements

- **Andreas F. Clenow** — author of *Trading Evolved*, original compiler of the 1996–2019 S&P 500 constituent history
- **Norgate Data** — the underlying data provider for the original S&P 500 history
- **fja05680** — creator of [fja05680/sp500](https://github.com/fja05680/sp500), which provided the foundation, format, and methodology for this project
- **Wikipedia contributors** — for maintaining the historical components pages that make ongoing automation possible
- **NSE Indices Limited** — for publishing official constituent lists

---

## Related Projects

- [fja05680/sp500](https://github.com/fja05680/sp500) — the original S&P 500 constituent tracker that inspired this project
- [yfiua/index-constituents](https://github.com/yfiua/index-constituents) — another open-source index constituent dataset (Apache-2.0)
- [hanshof/sp500_constituents](https://github.com/hanshof/sp500_constituents) — S&P 500 constituents from 1996 to present
