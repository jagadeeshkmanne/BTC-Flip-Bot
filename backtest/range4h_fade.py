"""range4h_fade.py — the '4-hour range' false-breakout fade, exactly as specified.
(user 2026-06-12, YouTube strategy)

Rules: mark high/low of the FIRST 4h candle of the day (NY anchor). On 5m:
a candle CLOSES outside the range, then a later candle CLOSES back inside ->
fade entry at next bar open (short after re-entry from above, long from below).
SL = the extreme of the breakout excursion. TP = 2x the SL distance. Multiple
setups per day until the day ends; one position at a time. Honest fills,
fees+slip 0.075%/side, pessimistic SL-before-TP.
Creator's sample: 7 trades, 5W/2L, '+8R, 72% WR'. We run EVERY day, 7 years.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
RT = 2 * (0.00055 + 0.0002)

df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
O, H, L, C = (df[k].values for k in ("open", "high", "low", "close"))
TS = df["timestamp"]
n = len(df)


def run(anchor_utc):
    """anchor_utc: hour when the day's first 4h candle OPENS (NY midnight ~ 4 or 5 UTC)."""
    # day id = date of (ts - anchor) so each 'day' starts at the anchor
    shifted = TS - pd.Timedelta(hours=anchor_utc)
    dayid = shifted.dt.date.values
    hrs = TS.dt.hour.values
    trades = []
    i = 0
    cur_day = None
    rng_hi = rng_lo = None
    rng_done_i = -1
    state = "wait_range"
    out_dir = 0          # +1 broke below (arm long), -1 broke above (arm short)
    ext = 0.0            # breakout excursion extreme
    pos = 0; entry = sl = tp = 0.0
    while i < n - 1:
        d = dayid[i]
        if d != cur_day:                      # new day: reset, find the first 4h window
            cur_day = d; state = "in_range_candle"
            rh = -1e18; rl = 1e18; rng_hi = rng_lo = None
            day_start_i = i
            pos = 0
        # build the first 4h candle (anchor..anchor+4h)
        if state == "in_range_candle":
            t_in_day = (TS[i] - pd.Timedelta(hours=anchor_utc)) - pd.Timestamp(d)
            if t_in_day < pd.Timedelta(hours=4):
                rh = max(rh, H[i]); rl = min(rl, L[i])
                i += 1; continue
            rng_hi, rng_lo = rh, rl
            state = "inside"
        if pos != 0:
            # manage open fade: SL first (pessimistic), then 2R TP
            if pos == 1:
                if L[i] <= sl: trades.append((TS[i], (min(O[i], sl)/entry - 1) - RT)); pos = 0
                elif H[i] >= tp: trades.append((TS[i], (max(O[i], tp)/entry - 1) - RT)); pos = 0
            else:
                if H[i] >= sl: trades.append((TS[i], (1 - max(O[i], sl)/entry) - RT)); pos = 0
                elif L[i] <= tp: trades.append((TS[i], (1 - min(O[i], tp)/entry) - RT)); pos = 0
            i += 1; continue
        # flat, range known: watch for breakout-close then re-entry-close
        if state == "inside":
            if C[i] > rng_hi: state = "outside"; out_dir = -1; ext = H[i]
            elif C[i] < rng_lo: state = "outside"; out_dir = 1; ext = L[i]
        elif state == "outside":
            ext = max(ext, H[i]) if out_dir == -1 else min(ext, L[i])
            back_in = rng_lo < C[i] < rng_hi
            if back_in:
                entry = O[i+1]
                if out_dir == -1:             # broke above, re-entered -> SHORT
                    sl = ext; risk = sl - entry
                    if risk > 0:
                        tp = entry - 2*risk; pos = -1
                else:                          # broke below -> LONG
                    sl = ext; risk = entry - sl
                    if risk > 0:
                        tp = entry + 2*risk; pos = 1
                state = "inside"
        i += 1
    return pd.DataFrame(trades, columns=["t", "net"])


for anchor, lab in [(4, "NY midnight (UTC-4)"), (5, "NY midnight (UTC-5)"), (0, "UTC midnight")]:
    r = run(anchor)
    rec = r[r["t"] >= pd.Timestamp("2025-01-01")]["net"]
    wr = (r.net > 0).mean()*100
    print(f"anchor={lab:22s} trades={len(r):6d}  WR={wr:4.1f}%  (2R needs >34.8% to break even gross)")
    print(f"   gross={(r.net.mean()+RT)*100:+.4f}%/t  net={r.net.mean()*100:+.4f}%/t  "
          f"compounded={((1+r.net).prod()-1)*100:+.0f}%  2025-26 net={rec.mean()*100 if len(rec)>20 else float('nan'):+.4f}%")
    # per-year
    py = r.set_index("t")["net"].groupby(lambda x: x.year).mean()
    print("   net/trade by year: " + "  ".join(f"{y}:{v*100:+.3f}%" for y, v in py.items()))
