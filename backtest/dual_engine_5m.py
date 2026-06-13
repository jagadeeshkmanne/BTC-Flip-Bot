"""dual_engine_5m.py — the 'dual-engine' 5m system, exactly as specified.
Supertrend(10,3) + BB(20,2.5) + ADX(14). ADX>25: trend engine (ST flips, stop=ST
line, exhaustion exit at opposite outer band -> flip into MR trade). ADX<25: MR
engine (pierce outer band -> fade to mid-band, stop 1*ATR past breakout candle).
Honest: decisions on closed bars -> act next open; stops/targets intra-bar,
stop-before-target; fees+slip 0.075%/side. 7y BTC 5m.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
RT = 2 * (0.00055 + 0.0002)

df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
O, H, L, C = (df[k].values for k in ("open", "high", "low", "close"))
TS = df["timestamp"]; n = len(df)
cs, hs, ls = pd.Series(C), pd.Series(H), pd.Series(L)

# ATR(10) RMA + Supertrend(10,3)
pc = cs.shift(1)
tr = pd.concat([hs-ls, (hs-pc).abs(), (ls-pc).abs()], axis=1).max(axis=1)
atr10 = tr.ewm(alpha=1/10, adjust=False).mean().values
hl2 = (H + L) / 2
ub_raw = hl2 + 3*atr10; lb_raw = hl2 - 3*atr10
st_dir = np.zeros(n); st_line = np.full(n, np.nan)
ub = ub_raw.copy(); lb = lb_raw.copy()
for i in range(1, n):
    ub[i] = ub_raw[i] if (ub_raw[i] < ub[i-1] or C[i-1] > ub[i-1]) else ub[i-1]
    lb[i] = lb_raw[i] if (lb_raw[i] > lb[i-1] or C[i-1] < lb[i-1]) else lb[i-1]
    if st_dir[i-1] >= 0:
        st_dir[i] = -1 if C[i] < lb[i] else 1
    else:
        st_dir[i] = 1 if C[i] > ub[i] else -1
    st_line[i] = lb[i] if st_dir[i] == 1 else ub[i]
# BB(20,2.5)
mid = cs.rolling(20).mean().values; sd = cs.rolling(20).std().values
bb_u = mid + 2.5*sd; bb_l = mid - 2.5*sd
# ADX(14)
up = hs.diff(); dn = -ls.diff()
plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0))
minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0))
atr14 = tr.ewm(alpha=1/14, adjust=False).mean()
pdi = 100*plus.ewm(alpha=1/14, adjust=False).mean()/atr14
mdi = 100*minus.ewm(alpha=1/14, adjust=False).mean()/atr14
dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0, np.nan)
adx = dx.ewm(alpha=1/14, adjust=False).mean().values

trades = []   # (exit_ts, net, engine)
pos = 0; entry = 0.0; engine = None; mr_stop = 0.0
i = 30
while i < n - 2:
    if pos != 0:
        # --- manage open position on bar i ---
        if engine == "T":
            stop = st_line[i]
            flipped = (pos == 1 and st_dir[i] == -1) or (pos == -1 and st_dir[i] == 1)
            exhausted = (pos == 1 and H[i] >= bb_u[i]) or (pos == -1 and L[i] <= bb_l[i])
            if flipped:                                   # exit next open on flip
                px = O[i+1]
                trades.append((TS[i+1], ((px/entry-1)*pos) - RT, "T")); pos = 0
            elif exhausted:                               # exhaustion: close AND flip to MR
                px = bb_u[i] if pos == 1 else bb_l[i]
                px = max(O[i], px) if pos == 1 else min(O[i], px)
                trades.append((TS[i], ((px/entry-1)*pos) - RT, "T"))
                pos = -1 if pos == 1 else 1               # MR fade, entry at band touch px
                entry = px; engine = "M"
                mr_stop = (H[i] + atr10[i]) if pos == -1 else (L[i] - atr10[i])
        else:  # MR trade: stop first (pessimistic), then mid-band target
            hit_stop = (pos == 1 and L[i] <= mr_stop) or (pos == -1 and H[i] >= mr_stop)
            if hit_stop:
                px = min(O[i], mr_stop) if pos == 1 else max(O[i], mr_stop)
                trades.append((TS[i], ((px/entry-1)*pos) - RT, "M")); pos = 0
            else:
                tgt = mid[i]
                hit = (pos == 1 and H[i] >= tgt) or (pos == -1 and L[i] <= tgt)
                if hit and not np.isnan(tgt):
                    px = max(O[i], tgt) if pos == 1 else min(O[i], tgt)
                    trades.append((TS[i], ((px/entry-1)*pos) - RT, "M")); pos = 0
    if pos == 0:
        # --- flat: look for entries per the ADX traffic controller (closed bar i, enter i+1 open) ---
        if np.isnan(adx[i]) or np.isnan(bb_u[i]) or st_dir[i] == 0:
            i += 1; continue
        if adx[i] > 25:                                   # trend engine
            flip_up = st_dir[i] == 1 and st_dir[i-1] == -1
            flip_dn = st_dir[i] == -1 and st_dir[i-1] == 1
            if flip_up or flip_dn:
                pos = 1 if flip_up else -1; entry = O[i+1]; engine = "T"
        else:                                             # mean-reversion engine
            if L[i] <= bb_l[i]:
                pos = 1; entry = O[i+1]; engine = "M"
                mr_stop = L[i] - atr10[i]
            elif H[i] >= bb_u[i]:
                pos = -1; entry = O[i+1]; engine = "M"
                mr_stop = H[i] + atr10[i]
    i += 1

r = pd.DataFrame(trades, columns=["t", "net", "eng"])
print(f"DUAL-ENGINE 5m (Supertrend+BB2.5+ADX), honest, {TS.iloc[0].date()}->{TS.iloc[-1].date()}")
for eng, lab in [(None, "ALL"), ("T", "trend engine"), ("M", "mean-rev engine")]:
    s = r if eng is None else r[r.eng == eng]
    rec = s[s["t"] >= pd.Timestamp("2025-01-01")]["net"]
    eq = (1 + s["net"]).prod() - 1
    print(f"  {lab:16s} deals={len(s):6d}  WR={(s.net>0).mean()*100:4.1f}%  "
          f"gross={(s.net.mean()+RT)*100:+.4f}%/d  net={s.net.mean()*100:+.4f}%/d  "
          f"compounded={eq*100:+,.0f}%  2025-26={rec.mean()*100 if len(rec)>20 else float('nan'):+.4f}%")
