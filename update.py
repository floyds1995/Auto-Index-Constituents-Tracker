import pandas as pd
import requests
from io import StringIO

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

def find_col(df, *keywords):
    for c in df.columns:
        name = str(c).lower()
        if all(k.lower() in name for k in keywords):
            return c
    return None

def scrape_changes(url):
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; research)"})
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    table = tables[0]

    if isinstance(table.columns, pd.MultiIndex):
        flat = []
        for c in table.columns:
            parts = [str(x) for x in c if "Unnamed" not in str(x)]
            flat.append("_".join(parts).strip("_"))
        table.columns = flat

    first_row_str = " ".join(str(x) for x in table.iloc[0].tolist()).lower()
    if "ticker" in first_row_str and "security" in first_row_str:
        table = table.iloc[1:].reset_index(drop=True)

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

    # 🔧 Force dates to datetime, drop anything unparseable
    clean["date"] = pd.to_datetime(clean["date"], errors="coerce")
    clean = clean.dropna(subset=["date"])

    # 🔧 Force tickers to plain strings
    clean["added"]   = clean["added"].fillna("").astype(str).str.strip()
    clean["removed"] = clean["removed"].fillna("").astype(str).str.strip()

    return clean.reset_index(drop=True)

def process_index(config):
    name = config["name"]
    file = config["file"]
    url  = config["url"]

    print(f"\n{'='*60}")
    print(f"Processing {name}  ({file})")
    print(f"{'='*60}")

    # 🔧 Read file, force date as string first, then parse
    df = pd.read_csv(file, dtype=str)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    if df.empty:
        print("File is empty after date parsing. Skipping.")
        return

    last_date = pd.Timestamp(df["date"].iloc[-1])
    last_tickers_raw = str(df["tickers"].iloc[-1])
    current_tickers = {t.strip() for t in last_tickers_raw.split(",") if t.strip() and t.strip().lower() != "nan"}

    print(f"File ends at:    {last_date.date()}")
    print(f"Current tickers: {len(current_tickers)}")

    # 🔧 Scrape and force date types
    changes = scrape_changes(url)
    changes["date"] = pd.to_datetime(changes["date"], errors="coerce")
    changes = changes.dropna(subset=["date"])

    new_changes = changes[changes["date"] > last_date].sort_values("date").reset_index(drop=True)

    print(f"New change rows: {len(new_changes)}")

    if new_changes.empty:
        print("No updates needed.")
        return

    for _, row in new_changes.iterrows():
        # 🔧 Force everything to str before touching the set
        removed = str(row["removed"]).strip() if row["removed"] else ""
        added   = str(row["added"]).strip()   if row["added"]   else ""

        if removed and removed.lower() != "nan":
            current_tickers.discard(removed)
        if added and added.lower() != "nan":
            current_tickers.add(added)

        # 🔧 Ensure the set only contains strings
        current_tickers = {str(t).strip() for t in current_tickers if str(t).strip()}

        df.loc[len(df)] = {
            "date":    pd.Timestamp(row["date"]),
            "tickers": ",".join(sorted(current_tickers)),
        }

    # 🔧 Save with normalized date format
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df.to_csv(file, index=False)
    print(f"Saved. Total rows: {len(df)}. Final tickers: {len(current_tickers)}")

# ---------- Main ----------
for config in INDICES:
    try:
        process_index(config)
    except Exception as e:
        print(f"\n❌ ERROR processing {config['name']}: {e}")
