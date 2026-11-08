# repo/us_data/raw_ohlcv/update_us_ohlcv.py
import os
import re
import sys
import time
import random
import logging
import pandas as pd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import yfinance as yf
from datetime import datetime, timezone
from pathlib import Path

# ==========================================
# 1. CONFIGURATION & PATHS
# ==========================================
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "us_data" / "raw_ohlcv" / "parquet"
DATA_DIR.mkdir(parents=True, exist_ok=True)

NDX_CSV = ROOT / "NDX100" / "Nasdaq 100 Historical Components & Changes.csv"
SNP_CSV = ROOT / "SNP500" / "S&P 500 Historical Components & Changes.csv"

MANIFEST_PATH = ROOT / "us_data" / "raw_ohlcv" / "manifest.csv"
LOG_PATH = ROOT / "us_data" / "raw_ohlcv" / "update_log.csv"
ALIAS_PATH = ROOT / "us_data" / "raw_ohlcv" / "ticker_aliases.csv"

START_DATE = "2019-01-01"
END_DATE = datetime.now(timezone.utc).strftime('%Y-%m-%d')

TICKER_REGEX = re.compile(r'^(?=.*[A-Z])[A-Z0-9\.\-]{1,8}$')

def is_valid_ticker(sym):
    return bool(TICKER_REGEX.match(str(sym).strip().upper()))

# ==========================================
# 2. SCHEMA DEFINITION
# ==========================================
PARQUET_SCHEMA = pa.schema([
    ("Ticker",      pa.dictionary(pa.int32(), pa.string())),
    ("Date",        pa.timestamp("ns")),
    ("Open",        pa.float32()),
    ("High",        pa.float32()),
    ("Low",         pa.float32()),
    ("Close",       pa.float32()),
    ("Adj_Close",   pa.float32()),
    ("Volume",      pa.int64()),
    ("Split_Ratio", pa.float32()),
])

# ==========================================
# 3. HELPERS
# ==========================================
def parquet_path_for(sym):
    return DATA_DIR / f"{sym}.parquet"

def write_parquet(sym, df):
    df = df[["Ticker","Date","Open","High","Low","Close", "Adj_Close","Volume","Split_Ratio"]].copy()
    df["Date"] = pd.to_datetime(df["Date"]).astype("datetime64[ns]")
    pq.write_table(
        pa.Table.from_pandas(df, schema=PARQUET_SCHEMA, preserve_index=False),
        parquet_path_for(sym),
        compression="zstd",
        compression_level=9,
    )

def read_parquet(sym):
    p = parquet_path_for(sym)
    return pd.read_parquet(p) if p.exists() else None

def list_existing_tickers():
    return {f.stem for f in DATA_DIR.glob("*.parquet") if is_valid_ticker(f.stem)}

def _to_naive_norm(idx):
    dt = pd.DatetimeIndex(idx)
    if dt.tz is not None:
        dt = dt.tz_localize(None)
    return dt.normalize()

def _normalize_splits(ticker):
    try:
        s = ticker.splits
    except Exception:
        return pd.Series(dtype=float)
    if s is None or len(s) == 0:
        return pd.Series(dtype=float)
    s.index = _to_naive_norm(s.index)
    return s.astype(float)

def _history_to_schema(df, yf_sym, splits):
    if df is None or len(df) == 0:
        return None
    df = df.reset_index()
    dcol = "Date" if "Date" in df.columns else "Datetime"
    df["Date"] = _to_naive_norm(df[dcol])
    df = (df.drop_duplicates("Date", keep="last").sort_values("Date").reset_index(drop=True))
    df["Split_Ratio"] = 0.0
    
    if len(splits) > 0:
        sd = pd.DataFrame({"Date": splits.index, "Split_Ratio": splits.values})
        df = df.merge(sd, on="Date", how="left", suffixes=("", "_s"))
        df["Split_Ratio"] = df["Split_Ratio_s"].fillna(df["Split_Ratio"])
        df = df.drop(columns=["Split_Ratio_s"])
        
    return pd.DataFrame({
        "Ticker":      yf_sym,
        "Date":        df["Date"],
        "Open":        pd.to_numeric(df["Open"],      errors="coerce").astype("float32"),
        "High":        pd.to_numeric(df["High"],      errors="coerce").astype("float32"),
        "Low":         pd.to_numeric(df["Low"],       errors="coerce").astype("float32"),
        "Close":       pd.to_numeric(df["Close"],     errors="coerce").astype("float32"),
        "Adj_Close":   pd.to_numeric(df["Adj Close"], errors="coerce").astype("float32"),
        "Volume":      pd.to_numeric(df["Volume"],    errors="coerce").fillna(0).astype("int64"),
        "Split_Ratio": df["Split_Ratio"].astype("float32"),
    })

def fetch_with_retry(t, start, end, retries=3):
    for attempt in range(retries):
        try:
            df = t.history(start=start, end=end, auto_adjust=False)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass
        time.sleep(1.5)
    return pd.DataFrame()

# ==========================================
# 4. ALIAS GENERATION
# ==========================================
def ensure_aliases_exist():
    if ALIAS_PATH.exists():
        return
    print("Generating ticker_aliases.csv for the first time...", flush=True)
    try:
        import tirramind
        t_df = tirramind.delistings()
        t_df['ticker'] = t_df['ticker'].astype(str).str.upper().str.strip()
        aliases = []
        for _, row in t_df.iterrows():
            for col in ['succeeded_by', 'superseded_by']:
                if col in row and pd.notna(row[col]):
                    new_sym = str(row[col]).upper().strip()
                    if is_valid_ticker(new_sym) and new_sym not in ('NONE', 'NAN', 'UNKNOWN'):
                        aliases.append({'old_symbol': row['ticker'], 'new_symbol': new_sym})
                        break
        alias_df = pd.DataFrame(aliases).drop_duplicates('old_symbol')
        alias_df.to_csv(ALIAS_PATH, index=False)
        print(f"Generated {len(alias_df)} valid aliases.", flush=True)
    except Exception as e:
        print(f"Tirramind failed ({e}). Creating empty alias file.", flush=True)
        pd.DataFrame(columns=['old_symbol', 'new_symbol']).to_csv(ALIAS_PATH, index=False)

def load_aliases():
    ensure_aliases_exist()
    df = pd.read_csv(ALIAS_PATH)
    return {row.old_symbol: row.new_symbol for row in df.itertuples() if is_valid_ticker(row.new_symbol)}

# ==========================================
# 5. UPDATE LOGIC
# ==========================================
def upsert_ticker(sym, alias_map, force_full=False):
    existing = read_parquet(sym)
    
    original_sym = sym.replace(".", "-").upper().strip()
    candidates = [original_sym]
    
    if sym in alias_map:
        alias_sym = alias_map[sym].replace(".", "-").upper().strip()
        if alias_sym != original_sym and alias_sym not in candidates:
            candidates.append(alias_sym)

    if existing is None or force_full:
        for yf_sym in candidates:
            t = yf.Ticker(yf_sym)
            raw_df = fetch_with_retry(t, START_DATE, END_DATE)
            if not raw_df.empty:
                df = _history_to_schema(raw_df, yf_sym, _normalize_splits(t))
                write_parquet(sym, df)
                return {"symbol": sym, "status": "created", "rows": len(df)}
        return {"symbol": sym, "status": "no_data", "rows": 0}

    last_date = pd.Timestamp(existing["Date"].max())
    start_str = (last_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    
    for yf_sym in candidates:
        t = yf.Ticker(yf_sym)
        splits = _normalize_splits(t)
        divs = _to_naive_norm(t.dividends.index) if len(t.dividends) > 0 else pd.DatetimeIndex([])
        
        new_splits = splits[splits.index > last_date] if len(splits) > 0 else pd.Series()
        new_divs = divs[divs > last_date] if len(divs) > 0 else pd.DatetimeIndex([])

        # 1. Corporate Action Check (Full Rewrite)
        if len(new_splits) > 0 or len(new_divs) > 0:
            raw_df = fetch_with_retry(t, START_DATE, END_DATE)
            if not raw_df.empty:
                df = _history_to_schema(raw_df, yf_sym, splits)
                write_parquet(sym, df)
                return {"symbol": sym, "status": "rewritten_corp_action", "rows": len(df)}
            continue # If rewrite fails, try next candidate

        # 2. Tail Fetch
        tail_raw = fetch_with_retry(t, start_str, END_DATE)
        if not tail_raw.empty:
            tail = _history_to_schema(tail_raw, yf_sym, splits)
            comb = (pd.concat([existing, tail], ignore_index=True)
                      .drop_duplicates("Date", keep="last")
                      .sort_values("Date").reset_index(drop=True))
            write_parquet(sym, comb)
            return {"symbol": sym, "status": "updated", "rows": len(comb), "new_rows": len(tail)}
        
        # 3. CRITICAL FIX: If tail is empty, but we have an alias candidate we haven't tried yet...
        # We need to test if the ALIAS has newer data. If it does, we force a full rewrite.
        if yf_sym == original_sym and len(candidates) > 1:
            # Let the loop continue to try the next candidate (the alias)
            continue

    return {"symbol": sym, "status": "up_to_date", "rows": len(existing)}

# ==========================================
# 6. PARSE CSV UNIVERSE
# ==========================================
def get_active_universe():
    tickers = set()
    for csv_path in [NDX_CSV, SNP_CSV]:
        if not csv_path.exists():
            continue
        df = pd.read_csv(csv_path).dropna(how='all')
        if len(df) == 0:
            continue
        last_row = df.iloc[-1]
        raw_tickers = str(last_row.iloc[1]).split(",")
        for t in raw_tickers:
            t = t.strip().upper()
            if is_valid_ticker(t):
                tickers.add(t)
    return tickers

# ==========================================
# 7. MAIN EXECUTION
# ==========================================
def main():
    missing_only = "--missing-only" in sys.argv
    
    print("=" * 70, flush=True)
    print(f"STARTING US OHLCV PIPELINE (missing_only={missing_only})", flush=True)
    print(f"Data Range: {START_DATE} to {END_DATE}", flush=True)
    print("=" * 70, flush=True)
    
    alias_map = load_aliases()
    active_universe = get_active_universe()
    existing_tickers = list_existing_tickers()
    
    print(f"Active Universe: {len(active_universe)} tickers", flush=True)
    print(f"Existing Files:  {len(existing_tickers)} tickers", flush=True)
    
    missing_tickers = active_universe - existing_tickers
    print(f"Missing Files:   {len(missing_tickers)} tickers", flush=True)
    
    results = []
    start_time = time.time()
    
    if not missing_only:
        for i, sym in enumerate(existing_tickers):
            print(f"[UPD] {sym}...", flush=True)
            r = upsert_ticker(sym, alias_map)
            results.append(r)
            if (i + 1) % 50 == 0:
                time.sleep(random.uniform(2.0, 4.0))
                
    for i, sym in enumerate(missing_tickers):
        print(f"[NEW] {sym}...", flush=True)
        r = upsert_ticker(sym, alias_map, force_full=True)
        results.append(r)
        time.sleep(random.uniform(0.5, 1.5))
            
    print("\nRebuilding Manifest...", flush=True)
    manifest_rows = []
    for f in DATA_DIR.glob("*.parquet"):
        sym = f.stem
        if not is_valid_ticker(sym):
            continue
        df = pd.read_parquet(f, columns=["Date"])
        manifest_rows.append({
            "ticker": sym,
            "rows": len(df),
            "first_date": df["Date"].min().date().isoformat() if len(df) else None,
            "last_date": df["Date"].max().date().isoformat() if len(df) else None,
            "file_size_kb": round(f.stat().st_size / 1024, 1),
        })
        
    pd.DataFrame(manifest_rows).sort_values("ticker").to_csv(MANIFEST_PATH, index=False)
    pd.DataFrame(results).to_csv(LOG_PATH, index=False)
    
    print(f"\n✅ Complete in {(time.time() - start_time)/60:.1f} minutes.", flush=True)

if __name__ == "__main__":
    main()
