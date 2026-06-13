"""ny_session_range.py — NY-session opening range, one best trade per day.
(user 2026-06-12) Anchor the day at the NY SESSION OPEN (09:30 ET ~ 13:30 UTC),
mark the opening range (first 30m / 1h / 4h), then take the FIRST valid setup:
  FADE   (the video's method): 5m close outside -> close back inside -> fade,
         SL at the breakout extreme, TP 2R.
  FOLLOW (classic ORB): 5m close outside -> enter WITH the breakout,
         SL at the range midpoint, TP 2R.
One trade per day max. Day ends at next NY open; open trades closed at market.
Honest: entry next bar open, SL-first intrabar, fees+slip 0.075%/side.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
RT = 2 * (0.00055 + 0.0002)
ANCHOR = 13.5  # 09:30 ET in UTC (EDT); 14:30 EST variant noted

df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
O, H, L, C = (df[k].values for k in ("open", "high", "low", "close"))
TS = df["timestamp"]; n = len(df)
shifted = TS - pd.Timedelta(hours=ANCHOR)
dayid = shifted.dt.date.values
mins_in_day = ((shifted - shifted.dt.normalize()).dt.total_seconds() / 60).values


def run(range_min, mode):
    trades = []
    cur = None; rh = rl = None; done_day = False
    pos = 0; entry = sl = tp = 0.0
    state = "range"; out_dir = 0; ext = 0.0
    i = 0
    while i < n - 1:
        if dayid[i] != cur:
            if pos != 0:                                   # close at day roll, market
                px = O[i]
                trades.append((TS[i], ((px/entry - 1)*pos) - RT)); pos = 0
            cur = dayid[i]; rh = -1e18; rl = 1e18
            state = "range"; done_day = False
        if pos != 0:                                        # manage the day's trade
            if pos == 1:
                if L[i] <= sl: trades.append((TS[i], (min(O[i], sl)/entry - 1) - RT)); pos = 0; done_day = True
                elif H[i] >= tp: trades.append((TS[i], (max(O[i], tp)/entry - 1) - RT)); pos = 0; done_day = True
            else:
                if H[i] >= sl: trades.append((TS[i], (1 - max(O[i], sl)/entry) - RT)); pos = 0; done_day = True
                elif L[i] <= tp: trades.append((TS[i], (1 - min(O[i], tp)/entry) - RT)); pos = 0; done_day = True
            i += 1; continue
        if done_day:
            i += 1; continue
        m = mins_in_day[i]
        if state == "range":
            if m < range_min:
                rh = max(rh, H[i]); rl = min(rl, L[i]); i += 1; continue
            if rh < rl: i += 1; continue
            state = "inside"
        if state == "inside":
            if C[i] > rh: out_dir = +1; ext = H[i]; state = "outside"
            elif C[i] < rl: out_dir = -1; ext = L[i]; state = "outside"
            if state == "outside" and mode == "follow":     # ORB: enter WITH breakout now
                entry = O[i+1]
                mid = (rh + rl)/2
                if out_dir == 1:
                    sl = mid; risk = entry - sl
                    if risk > 0: tp = entry + 2*risk; pos = 1
                else:
                    sl = mid; risk = sl - entry
                    if risk > 0: tp = entry - 2*risk; pos = -1
                state = "inside"
        elif state == "outside":                            # fade waits for re-entry close
            ext = max(ext, H[i]) if out_dir == 1 else min(ext, L[i])
            if rl < C[i] < rh:
                entry = O[i+1]
                if out_dir == 1:                            # broke up, came back -> short
                    sl = ext; risk = sl - entry
                    if risk > 0: tp = entry - 2*risk; pos = -1
                else:
                    sl = ext; risk = entry - sl
                    if risk > 0: tp = entry + 2*risk; pos = 1
                state = "inside"
        i += 1
    return pd.DataFrame(trades, columns=["t", "net"])


print("NY-session-open anchored range (09:30 ET), ONE trade/day, TP=2R, honest:")
print(f"  {'range':>6} {'mode':>7} | {'days':>5} {'WR':>6} {'gross%/t':>9} {'net%/t':>9} {'comp':>8} {'2025-26':>9}")
for rng, rlab in [(30, "30m"), (60, "1h"), (240, "4h")]:
    for mode in ("fade", "follow"):
        r = run(rng, mode)
        rec = r[r["t"] >= pd.Timestamp("2025-01-01")]["net"]
        comp = ((1 + r.net).prod() - 1) * 100
        print(f"  {rlab:>6} {mode:>7} | {len(r):>5} {(r.net>0).mean()*100:>5.1f}% "
              f"{(r.net.mean()+RT)*100:>+8.4f}% {r.net.mean()*100:>+8.4f}% {comp:>+7.0f}% "
              f"{rec.mean()*100 if len(rec)>20 else float('nan'):>+8.4f}%")
