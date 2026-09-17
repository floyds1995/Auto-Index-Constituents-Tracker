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

if isinstance(changes_table.columns, pd.MultiIndex):
    flat = []
    for c in changes_table.columns:
        parts = [str(x) for x in c if "Unnamed" not in str(x)]
        flat.append("_".join(parts))
    changes_table.columns = flat

if "Ticker" in str(changes_table.iloc[0].tolist()):
    changes_table = changes_table.iloc[1:].reset_index(drop=True)

changes_table = changes_table.rename(columns={
    "Effective Date": "date",
    "Added_Ticker": "added",
    "Removed_Ticker": "removed",
})

# ---------- 3. Clean ----------
changes_clean = changes_table[["date", "added", "removed"]].copy()
changes_clean["date"] = pd.to_datetime(
    changes_clean["date"], errors="coerce", format="mixed"
).ffill()
changes_clean["added"]   = changes_clean["added"].astype(str).replace("nan", "").str.strip()
changes_clean["removed"] = changes_clean["removed"].astype(str).replace("nan", "").str.strip()
changes_clean = changes_clean.dropna(subset=["date"])

# ---------- 4. Filter to NEW changes ----------
new_changes = changes_clean[changes_clean["date"] > last_date].sort_values("date")

print(f"New change rows: {len(new_changes)}")
if not new_changes.empty:
    print(new_changes.to_string(index=False))

if new_changes.empty:
    print("No updates needed.")
    raise SystemExit(0)

# ---------- 5. Apply & append ----------
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