# Auto-Index-Constituents-Tracker

Automated daily tracker for historical index constituents. Currently supports **S&P 500** and **Nasdaq 100**.

The repository maintains two CSV files — one per index — each containing the complete membership of that index on every date a change occurred. A scheduled GitHub Action runs daily, scrapes the relevant Wikipedia change log, and appends new rows automatically when the index composition changes.

No servers, no cron jobs, no external services — just GitHub Actions and Wikipedia.

---

## What It Does

For each index, the repo stores a file in this format:

```
date,tickers
1996-01-02,"AAL,AAMRQ,AAPL,ABI,..."
1996-01-03,"AAL,AAMRQ,AAPL,ABI,..."
...
2026-08-18,"AAPL,ABNB,ADBE,..."
```

- **One row per date the index composition changed** — not every trading day
- **`tickers`** is the full, sorted, comma-separated list of constituents on that date
- Dates are normalized to ISO format (`YYYY-MM-DD`)
- The last row always represents the current composition

Each day at **06:00 UTC**, a GitHub Action:

1. Reads the last row of each CSV to find the most recent date
2. Scrapes the corresponding Wikipedia change-log table
3. Filters to changes newer than the last date
4. Appends one new row per change date
5. Commits the updates back to the repo

If nothing changed, the workflow exits cleanly with no commit.

---

## Supported Indices

| Index | Data File | Source |
|---|---|---|
| S&P 500 | `S&P 500 Historical Components & Changes.csv` | [Wikipedia: Historical components of the S&P 500](https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500) |
| Nasdaq 100 | `Nasdaq 100 Historical Components & Changes.csv` | [Wikipedia: Historical components of the Nasdaq-100](https://en.wikipedia.org/wiki/Historical_components_of_the_Nasdaq-100) |

Both sources list changes with columns for **Effective Date**, **Added Ticker**, and **Removed Ticker**, which is what the scraper relies on.

---

## Repository Structure

```
Auto-Index-Constituents-Tracker/
├── .github/workflows/
│   └── update.yml                          # Daily automation
├── update.py                                # Unified update script
├── requirements.txt                         # Python dependencies
├── S&P 500 Historical Components & Changes.csv
├── Nasdaq 100 Historical Components & Changes.csv
└── README.md
```

---

## How It Works

The script (`update.py`) loops through a list of index configurations. Each config specifies:

- A display name
- The CSV file to update
- The Wikipedia URL to scrape

For each index, it:

1. Loads the CSV and determines the last recorded date
2. Parses the latest ticker list from the last row
3. Scrapes the Wikipedia change table into a clean DataFrame
4. Filters to changes strictly newer than the last date
5. Applies each change in chronological order — removing tickers that left, adding tickers that joined
6. Appends a new row to the CSV for each change date
7. Overwrites the file with normalized date formatting

The script is idempotent: re-running it with no new Wikipedia changes produces no output. This makes it safe to run on a schedule without duplicate entries.

---

## Data Format

### Input CSV (what the script reads)

Two columns: `date` and `tickers`.

```csv
date,tickers
2007-02-01,"AAPL,ADBE,ADSK,AEOS,..."
2007-02-14,"AAPL,ADBE,ADSK,AEOS,..."
```

### Output CSV (what the script writes)

Same format, with new rows appended and all dates normalized to `YYYY-MM-DD`.

### Index Configuration

Defined at the top of `update.py`:

```python
INDICES = [
    {
        "name": "S&P 500",
        "file": "S&P 500 Historical Components & Changes.csv",
        "url":  "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500",
    },
    {
        "name": "Nasdaq 100",
        "file": "Nasdaq 100 Historical Components & Changes.csv",
        "url":  "https://en.wikipedia.org/wiki/Historical_components_of_the_Nasdaq-100",
    },
]
```

To add a new index, append another dictionary with the same keys.

---

## Automated Schedule

The workflow is defined in `.github/workflows/update.yml`:

```yaml
on:
  schedule:
    - cron: '0 6 * * *'      # Daily at 06:00 UTC
  workflow_dispatch:          # Manual trigger from the Actions tab
```

- **Scheduled runs** happen daily at 06:00 UTC (11:30 AM IST)
- **Manual runs** can be triggered anytime from the **Actions** tab → **Update Index Constituents** → **Run workflow**
- GitHub Actions schedules are always in UTC and may be delayed by 5–30 minutes during peak load

---

## Setup

### Prerequisites

- A GitHub account
- A free or paid GitHub Actions plan (free is sufficient — this workflow uses roughly 30 minutes of Actions time per month)

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

3. Run the update script:
   ```bash
   python update.py
   ```

### GitHub Setup

1. Ensure `.github/workflows/update.yml` exists on the default branch (`main`)
2. Go to **Settings → Actions → General → Workflow permissions**
3. Select **"Read and write permissions"**
4. Click **Save**

Without this step, the workflow will run but will fail to push commits back to the repository.

---

## Adding a New Index

1. Obtain or create a CSV in the `date,tickers` format
2. Add it to the repo root
3. Add a new entry to the `INDICES` list in `update.py`
4. Commit and push

The script will automatically process the new index on the next run — no new workflow file, no new script.

**Note:** The Wikipedia page for your index must have a change-log table with columns for date, added ticker, and removed ticker. The scraper locates these columns by keyword, so minor variations in header names are tolerated.

---

## Manual Trigger

To run the update immediately without waiting for the schedule:

1. Go to the **Actions** tab
2. Click **Update Index Constituents** in the left sidebar
3. Click **Run workflow** → **Run workflow**

The workflow will run in roughly 30–60 seconds.

---

## Credits & Data Sources

This project would not exist without the work of others.

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

Both indices rely on Wikipedia's community-maintained change logs:

- **S&P 500:** [Historical components of the S&P 500](https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500)
- **Nasdaq 100:** [Historical components of the Nasdaq-100](https://en.wikipedia.org/wiki/Historical_components_of_the_Nasdaq-100)

Wikipedia content is available under the [Creative Commons Attribution-ShareAlike 4.0 International License](https://creativecommons.org/licenses/by-sa/4.0/).

**Important caveat:** Wikipedia's change logs are **not exhaustive**. They show *selected* changes and are sometimes incomplete, delayed, or contain errors. They are a helpful starting point but should not be treated as an authoritative source for regulatory or financial decisions.

### Nasdaq 100 Historical Data

The initial Nasdaq 100 constituent history in this repository was compiled from the Wikipedia historical components page linked above. Unlike the S&P 500 data, it does not derive from a commercial dataset. Users should be aware that:

- Coverage begins in **February 2007**
- Wikipedia's Nasdaq 100 change log may be less complete than the S&P 500 one
- The data has not been independently verified against a commercial source

---

## Disclaimer

This repository is provided **for research and educational purposes only**.

- The constituent data is assembled from community-maintained sources and **may contain errors, omissions, or inconsistencies**
- Wikipedia's change logs are not guaranteed to be complete, accurate, or timely
- The S&P 500 data traces back to a commercial data provider (Norgate Data) via a book download; redistribution or commercial use may be subject to additional terms
- Nothing here constitutes financial advice

If you are using this data for quantitative backtesting, be aware of the risks of **survivorship bias** and **look-ahead bias**. This dataset is specifically designed to help mitigate survivorship bias by preserving the full historical membership list — but only if the data is correct.

**Always verify against an authoritative source before making financial decisions.**

---

## License

The **code** in this repository (`update.py`, workflow files) is available under the MIT License.

The **data** in this repository is subject to the terms of its upstream sources:

- S&P 500 historical data (1996–2019): originally from *Trading Evolved* by Andreas Clenow / Norgate Data
- S&P 500 change data (2019–present) and format: derived from [fja05680/sp500](https://github.com/fja05680/sp500) (MIT License)
- Wikipedia-derived change data: CC BY-SA 4.0
- Nasdaq 100 historical data: Wikipedia, CC BY-SA 4.0

Users are responsible for complying with all applicable upstream licenses.

---

## Acknowledgements

- **Andreas F. Clenow** — author of *Trading Evolved*, original compiler of the 1996–2019 S&P 500 constituent history
- **Norgate Data** — the underlying data provider for the original S&P 500 history
- **fja05680** — creator of [fja05680/sp500](https://github.com/fja05680/sp500), which provided the foundation, format, and methodology for this project
- **Wikipedia contributors** — for maintaining the historical components pages that make ongoing automation possible

---

## Contributing

Issues and pull requests are welcome. If you spot an error in the historical data, please open an issue with:

1. The index name
2. The date in question
3. What the correct composition should be
4. A reference (Wikipedia revision, official announcement, etc.)

---

## Related Projects

- [fja05680/sp500](https://github.com/fja05680/sp500) — the original S&P 500 constituent tracker that inspired this project
- [yfiua/index-constituents](https://github.com/yfiua/index-constituents) — another open-source index constituent dataset (Apache-2.0)
- [hanshof/sp500_constituents](https://github.com/hanshof/sp500_constituents) — S&P 500 constituents from 1996 to present
