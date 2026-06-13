"""fetch_funding.py — pull full BTCUSDT funding-rate history from Bybit (8h cadence)."""
import json, time, urllib.request

SYM = "BTCUSDT"
OUT = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_funding.csv"
URL = "https://api.bybit.com/v5/market/funding/history?category=linear&symbol={}&limit=200&endTime={}"

rows = {}
end = int(time.time() * 1000)
for _ in range(60):
    u = URL.format(SYM, end)
    try:
        with urllib.request.urlopen(u, timeout=20) as r:
            data = json.load(r)
    except Exception as e:
        print("err", e); break
    lst = data.get("result", {}).get("list", [])
    if not lst:
        break
    for it in lst:
        ts = int(it["fundingRateTimestamp"])
        rows[ts] = float(it["fundingRate"])
    oldest = min(int(it["fundingRateTimestamp"]) for it in lst)
    if oldest >= end:
        break
    end = oldest - 1
    time.sleep(0.25)

items = sorted(rows.items())
import datetime as dt
with open(OUT, "w") as f:
    f.write("timestamp,funding_rate\n")
    for ts, fr in items:
        f.write(f"{dt.datetime.utcfromtimestamp(ts/1000).isoformat()},{fr}\n")
print(f"saved {len(items)} funding points -> {OUT}")
if items:
    a = dt.datetime.utcfromtimestamp(items[0][0]/1000).date()
    b = dt.datetime.utcfromtimestamp(items[-1][0]/1000).date()
    frs = [v for _, v in items]
    print(f"range {a} -> {b}   funding: min {min(frs)*100:.4f}%  max {max(frs)*100:.4f}%  "
          f"mean {sum(frs)/len(frs)*100:.4f}% (per 8h)")
