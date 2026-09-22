import requests

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.nseindia.com/",
})

# Test 1: OLD endpoint (2018) — should work
url_old = "https://nsearchives.nseindia.com/content/historical/EQUITIES/2018/JAN/cm01JAN2018bhav.csv.zip"
r = session.get(url_old, timeout=30)
print(f"OLD 2018: status={r.status_code}, bytes={len(r.content)}")

# Test 2: NEW endpoint (2026) — the one that fails
url_new = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_21092026.csv"
r = session.get(url_new, timeout=30)
print(f"NEW 2026: status={r.status_code}, bytes={len(r.content)}")

# Test 3: NEW endpoint via www.nseindia.com (different domain, possibly different WAF)
url_www = "https://www.nseindia.com/api/reports?archives=equities&type=sec_bhavdata_full&date=21092026&csv=true"
r = session.get(url_www, timeout=30)
print(f"WWW API:  status={r.status_code}, bytes={len(r.content)}")
