import pandas as pd
import requests
from io import StringIO

CSV_FILE = "S&P 500 Historical Components & Changes.csv"
URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"

# ---------- 1. Load current file ----------
df = pd.read_csv(CSV_FILE)
df["date"] = pd.to_datetime(df["date"])
df = df.sort_values("date").reset_index(drop=True)

last_date = df["date"].iloc[-1]
current_tickers = {t.strip() for t in str(df["tickers"].iloc[-1]).split(",") if t.strip()}

print(f"File ends at: {last_date.date()}")
print(f"Current tickers: {len(current_tickers)}")

# ---------- 2. Scrape Wikipedia change log ----------
resp = requests.get(URL, headers={"User-Agent": "Mozilla/5.0 (compatible; research)"})
resp.raise_for_status()
tables = pd.read_html(StringIO(resp.text))
changes_table = tables[0]

# Flatten MultiIndex columns
if isinstance(changes_table.columns, pd.MultiIndex):
    flat = []
    for c in changes_table.columns:
        parts = [str(x) for x in c if "Unnamed" not in str(x)]
        flat.append("_".join(parts).strip("_"))
    changes_table.columns = flat

# 🔍 DEBUG: show what columns we actually got
print("DEBUG columns:", changes_table.columns.tolist())
print("DEBUG first row:", changes_table.iloc[0].tolist())

# Drop duplicate header row if present
first_row_str = " ".join(str(x) for x in changes_table.iloc[0].tolist()).lower()
if "ticker" in first_row_str and "security" in first_row_str:
    changes_table = changes_table.iloc[1:].reset_index(drop=True)

# ---------- 3. Find the right columns by keyword ----------
def find_col(df, *keywords):
    """Find a column whose name contains ALL given keywords (case-insensitive)."""
    for c in df.columns:
        name = str(c).lower()
        if all(k.lower() in name for k in keywords):
            return c
    return None

date_col    = find_col(changes_table, "date")
added_col   = find_col(changes_table, "added", "ticker")
removed_col = find_col(changes_table, "removed", "ticker")

# Fallback: if "added"+"ticker" fails, just find any column containing "ticker"
ticker_cols = [c for c in changes_table.columns if "ticker" in str(c).lower()]
if added_col is None and len(ticker_cols) >= 1:
    added_col = ticker_cols[0]
if removed_col is None and len(ticker_cols) >= 2:
    removed_col = ticker_cols[1]

# Fallback for date: first column
if date_col is None:
    date_col = changes_table.columns[0]

print(f"DEBUG using -> date={date_col!r}, added={added_col!r}, removed={removed_col!r}")

if date_col is None or added_col is None or removed_col is None:
    raise RuntimeError(f"Could not locate columns. Available: {changes_table.columns.tolist()}")

# ---------- 4. Clean ----------
changes_clean = changes_table[[date_col, added_col, removed_col]].copy()
changes_clean.columns = ["date", "added", "removed"]

changes_clean["date"] = pd.to_datetime(
    changes_clean["date"], errors="coerce", format="mixed"
).ffill()
changes_clean["added"]   = changes_clean["added"].astype(str).replace("nan", "").str.strip()
changes_clean["removed"] = changes_clean["removed"].astype(str).replace("nan", "").str.strip()
changes_clean = changes_clean.dropna(subset=["date"])

print(f"DEBUG parsed {len(changes_clean)} change rows")
print("DEBUG sample:")
print(changes_clean.head(5).to_string(index=False))

# ---------- 5. Filter to NEW changes ----------
new_changes = changes_clean[changes_clean["date"] > last_date].sort_values("date")

print(f"New change rows: {len(new_changes)}")
if not new_changes.empty:
    print(new_changes.to_string(index=False))

if new_changes.empty:
    print("No updates needed.")
    raise SystemExit(0)

# ---------- 6. Apply & append ----------
for _, row in new_changes.iterrows():
    if row["removed"]:
        current_tickers.discard(row["removed"])
    if row["added"]:
        current_tickers.add(row["added"])

    df.loc[len(df)] = {
        "date":    row["date"],
        "tickers": ",".join(sorted(current_tickers)),
    }

df.to_csv(CSV_FILE, index=False)
print(f"Saved. Total rows: {len(df)}. Final tickers: {len(current_tickers)}")
