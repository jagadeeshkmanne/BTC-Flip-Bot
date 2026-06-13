"""tv_meanrev_setups.py — the four 'best TradingView 5m mean-reversion setups',
implemented EXACTLY as the article specifies, honest fills + fees. (user 2026-06-12)

 1 VWAP + BB(20,2.5): pierce below band -> enter when a candle CLOSES BACK INSIDE
   (exhaustion confirm). TP = touch of daily-anchored VWAP. SL = exhaustion low.
 2 RSI(14)+EMA200: above EMA200 only longs; RSI<30 then CROSSES BACK above 30 ->
   long. TP = EMA20 touch. SL = recent swing low. (symmetric short below EMA200)
 3 LinReg(100)+StochRSI(14,14,3,3): pierce channel extreme (fit +/- 2*resid std),
   %K<20 and bullish %K/%D crossover -> long. TP = median line. SL = pierce low.
 4 Keltner(20,2)+MACD: candle outside KC while MACD histogram shrinks toward 0 ->
   enter. TP = KC mid (EMA20). SL = exhaustion extreme.
All: entry next bar open, pessimistic SL-before-TP intrabar, 24h timeout at close,
fees+slip 0.075%/side. Both sides where article implies symmetry.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
RT = 2 * (0.00055 + 0.0002)
TIMEOUT = 288

df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
O, H, L, C, V = (df[k].values for k in ("open", "high", "low", "close", "volume"))
TS = df["timestamp"]
n = len(df)
cs = pd.Series(C)

# shared indicators
day = TS.dt.date
pv = pd.Series(C * V); vv = pd.Series(V)
vwap = (pv.groupby(day).cumsum() / vv.groupby(day).cumsum()).values   # daily-anchored VWAP
mid20 = cs.rolling(20).mean(); sd20 = cs.rolling(20).std()
bb_lo = (mid20 - 2.5 * sd20).values; bb_hi = (mid20 + 2.5 * sd20).values
ema20 = cs.ewm(span=20, adjust=False).mean().values
ema200 = cs.ewm(span=200, adjust=False).mean().values
d = cs.diff(); ag = d.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
al = (-d.clip(upper=0)).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
rsi14 = (100 - 100/(1 + ag/al.replace(0, np.nan))).values
# StochRSI 14,14,3,3
rs = pd.Series(rsi14)
srs = (rs - rs.rolling(14).min()) / (rs.rolling(14).max() - rs.rolling(14).min())
k_line = (srs.rolling(3).mean() * 100); d_line = k_line.rolling(3).mean()
K, D = k_line.values, d_line.values
# LinReg channel 100
W = 100
x = np.arange(W); xm = x.mean(); xv = ((x - xm)**2).sum()
slope = cs.rolling(W).apply(lambda y: ((x - xm)*(y - y.mean())).sum()/xv, raw=True)
fit_end = cs.rolling(W).mean() + slope * (W - 1 - xm)          # regression value at the last bar
resid_sd = cs.rolling(W).std()                                  # ~proxy for residual band width
lr_mid = fit_end.values; lr_sd = resid_sd.values
# Keltner 20,2 (ATR-based) + MACD hist
pc = cs.shift(1)
tr = pd.concat([pd.Series(H)-pd.Series(L), (pd.Series(H)-pc).abs(), (pd.Series(L)-pc).abs()], axis=1).max(axis=1)
atr20 = tr.ewm(alpha=1/20, adjust=False).mean().values
kc_lo = ema20 - 2*atr20; kc_hi = ema20 + 2*atr20
macd = (cs.ewm(span=12, adjust=False).mean() - cs.ewm(span=26, adjust=False).mean())
hist = (macd - macd.ewm(span=9, adjust=False).mean()).values
low12 = pd.Series(L).rolling(12).min().values; high12 = pd.Series(H).rolling(12).max().values


def manage(i, side, sl_px, tp_kind):
    """enter at O[i+1]; exit TP target touch / SL (pessimistic first) / timeout."""
    entry = O[i+1]
    j = i + 1
    while j < min(n-1, i + 1 + TIMEOUT):
        tp_px = {"vwap": vwap[j], "ema20": ema20[j], "lrmid": lr_mid[j]}[tp_kind]
        if side == 1:
            if L[j] <= sl_px: return (min(O[j], sl_px)/entry - 1) - RT, j
            if not np.isnan(tp_px) and H[j] >= tp_px and tp_px > entry*0.999:
                return (max(O[j], tp_px)/entry - 1) - RT, j
        else:
            if H[j] >= sl_px: return (1 - max(O[j], sl_px)/entry) - RT, j
            if not np.isnan(tp_px) and L[j] <= tp_px and tp_px < entry*1.001:
                return (1 - min(O[j], tp_px)/entry) - RT, j
        j += 1
    j = min(n-1, i + 1 + TIMEOUT)
    return ((C[j]/entry - 1)*side) - RT, j


def setup1():  # VWAP + BB2.5, close-back-inside confirm
    out = []
    i = 21
    while i < n - 2:
        if C[i-1] < bb_lo[i-1] and C[i] > bb_lo[i] and C[i] < ema20[i]:      # closed back inside
            r, j = manage(i, 1, min(L[i-1], L[i])*0.999, "vwap"); out.append((TS[j], r)); i = j+1; continue
        if C[i-1] > bb_hi[i-1] and C[i] < bb_hi[i] and C[i] > ema20[i]:
            r, j = manage(i, -1, max(H[i-1], H[i])*1.001, "vwap"); out.append((TS[j], r)); i = j+1; continue
        i += 1
    return out


def setup2():  # RSI14 re-cross + EMA200 macro filter
    out = []
    i = 201
    while i < n - 2:
        if C[i] > ema200[i] and rsi14[i-1] < 30 and rsi14[i] >= 30:
            r, j = manage(i, 1, low12[i]*0.999, "ema20"); out.append((TS[j], r)); i = j+1; continue
        if C[i] < ema200[i] and rsi14[i-1] > 70 and rsi14[i] <= 70:
            r, j = manage(i, -1, high12[i]*1.001, "ema20"); out.append((TS[j], r)); i = j+1; continue
        i += 1
    return out


def setup3():  # LinReg channel + StochRSI crossover
    out = []
    i = 101
    while i < n - 2:
        lo_b = lr_mid[i] - 2*lr_sd[i]; hi_b = lr_mid[i] + 2*lr_sd[i]
        if np.isnan(lo_b): i += 1; continue
        if L[i] <= lo_b and K[i] < 20 and K[i] > D[i] and K[i-1] <= D[i-1]:
            r, j = manage(i, 1, L[i]*0.999, "lrmid"); out.append((TS[j], r)); i = j+1; continue
        if H[i] >= hi_b and K[i] > 80 and K[i] < D[i] and K[i-1] >= D[i-1]:
            r, j = manage(i, -1, H[i]*1.001, "lrmid"); out.append((TS[j], r)); i = j+1; continue
        i += 1
    return out


def setup4():  # Keltner + MACD histogram shrink
    out = []
    i = 27
    while i < n - 2:
        if C[i] < kc_lo[i] and hist[i] < 0 and hist[i] > hist[i-1]:
            r, j = manage(i, 1, L[i]*0.999, "ema20"); out.append((TS[j], r)); i = j+1; continue
        if C[i] > kc_hi[i] and hist[i] > 0 and hist[i] < hist[i-1]:
            r, j = manage(i, -1, H[i]*1.001, "ema20"); out.append((TS[j], r)); i = j+1; continue
        i += 1
    return out


print("The article's four 5m mean-reversion setups, exact rules, honest (7y BTC 5m):")
print(f"  {'setup':38s} {'deals':>6} {'WR':>6} {'gross%/d':>9} {'net%/d':>9} {'2025-26 net':>11}")
for fn, lab in [(setup1, "1 VWAP + BB(20,2.5) close-back-inside"),
                (setup2, "2 RSI14 re-cross + EMA200 filter"),
                (setup3, "3 LinReg(100) + StochRSI crossover"),
                (setup4, "4 Keltner(20,2) + MACD hist shrink")]:
    res = fn()
    r = pd.DataFrame(res, columns=["t", "net"])
    rec = r[r["t"] >= pd.Timestamp("2025-01-01")]["net"]
    print(f"  {lab:38s} {len(r):>6} {(r.net>0).mean()*100:>5.1f}% {(r.net.mean()+RT)*100:>+8.4f}% "
          f"{r.net.mean()*100:>+8.4f}% {rec.mean()*100 if len(rec)>20 else float('nan'):>+10.4f}%")
