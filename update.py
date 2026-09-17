import pandas as pd
import requests
from io import StringIO

# ---------- Config for each index ----------
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

# ---------- Shared helpers ----------
def find_col(df, *keywords):
    for c in df.columns:
        name = str(c).lower()
        if all(k.lower() in name for k in keywords):
            return c
    return None

def scrape_changes(url):
    """Scrape the Wikipedia change-log table and return a clean DataFrame."""
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; research)"})
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    table = tables[0]

    # Flatten MultiIndex columns
    if isinstance(table.columns, pd.MultiIndex):
        flat = []
        for c in table.columns:
            parts = [str(x) for x in c if "Unnamed" not in str(x)]
            flat.append("_".join(parts).strip("_"))
        table.columns = flat

    # Drop the duplicate header row if present
    first_row_str = " ".join(str(x) for x in table.iloc[0].tolist()).lower()
    if "ticker" in first_row_str and "security" in first_row_str:
        table = table.iloc[1:].reset_index(drop=True)

    # Find columns by keyword
    date_col    = find_col(table, "date")
    added_col   = find_col(table, "added", "ticker")
    removed_col = find_col(table, "removed", "ticker")

    ticker_cols = [c for c in table.columns if "ticker" in str(c).lower()]
    if added_col is None and len(ticker_cols) >= 1:
        added_col = ticker_cols[0]
    if removed_col is None and len(ticker_cols) >= 2:
        removed_col = ticker_cols[1]
    if date_col is None:
        date_col = table.columns[0]

    if date_col is None or added_col is None or removed_col is None:
        raise RuntimeError(f"Could not locate columns. Available: {table.columns.tolist()}")

    clean = table[[date_col, added_col, removed_col]].copy()
    clean.columns = ["date", "added", "removed"]

    clean["date"] = pd.to_datetime(clean["date"], errors="coerce", format="mixed").ffill()
    clean["added"]   = clean["added"].astype(str).replace("nan", "").str.strip()
    clean["removed"] = clean["removed"].astype(str).replace("nan", "").str.strip()
    clean = clean.dropna(subset=["date"])

    return clean

def process_index(config):
    """Load one CSV, apply any new changes, save it back."""
    name = config["name"]
    file = config["file"]
    url  = config["url"]

    print(f"\n{'='*60}")
    print(f"Processing {name}  ({file})")
    print(f"{'='*60}")

    df = pd.read_csv(file)
    df["date"] = pd.to_datetime(df["date"], format="mixed")
    df = df.sort_values("date").reset_index(drop=True)

    last_date = df["date"].iloc[-1]
    current_tickers = {t.strip() for t in str(df["tickers"].iloc[-1]).split(",") if t.strip()}

    print(f"File ends at:    {last_date.date()}")
    print(f"Current tickers: {len(current_tickers)}")

    changes = scrape_changes(url)
    new_changes = changes[changes["date"] > last_date].sort_values("date")

    print(f"New change rows: {len(new_changes)}")

    if new_changes.empty:
        print("No updates needed.")
        return

    for _, row in new_changes.iterrows():
        if row["removed"]:
            current_tickers.discard(row["removed"])
        if row["added"]:
            current_tickers.add(row["added"])

        df.loc[len(df)] = {
            "date":    row["date"],
            "tickers": ",".join(sorted(current_tickers)),
        }

    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df.to_csv(file, index=False)
    print(f"Saved. Total rows: {len(df)}. Final tickers: {len(current_tickers)}")

# ---------- Main ----------
for config in INDICES:
    try:
        process_index(config)
    except Exception as e:
        print(f"\n❌ ERROR processing {config['name']}: {e}")
        # Continue to next index instead of aborting
