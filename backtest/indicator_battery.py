"""indicator_battery.py — honest daily sweep of the common TradingView indicators.

Question (user 2026-06-12): of all the TV indicators (EMA/SMA/MACD/RSI/BB/
Donchian/ADX/Stoch/ROC/price-action...), which — single or combined — actually
fits BTC after honest costs?

Method (matches the repo's honest methodology; daily = the only TF with real
information per FINDINGS #2/#4):
  - Daily BTCUSDT, 2019-2026.
  - Each indicator -> a POSITION signal on the CLOSED daily bar.
  - Position taken on the NEXT bar (signal.shift(1)) — no lookahead.
  - Strategy return = pos[t-1] * dailyret[t]  -  cost * |pos change|.
  - Cost = 0.075%/side (0.055 taker + 0.02 slip), spot.
  - Two stances: LONG/FLAT (spot, 1/0) and LONG/SHORT (1/-1).
  - Metrics: net CAGR, Sharpe(ann), maxDD, exposure, #flips, and net CAGR in
    each half (IS/OOS). A real edge is POSITIVE IN BOTH HALVES and beats
    buy & hold on risk-adjusted terms — not just total return.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
COST = 0.00075
ANN = 365.0


def load_daily():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    g = pd.DataFrame({"open": df["open"].resample("1D").first(),
                      "high": df["high"].resample("1D").max(),
                      "low": df["low"].resample("1D").min(),
                      "close": df["close"].resample("1D").last(),
                      "vol": df["volume"].resample("1D").sum()}).dropna()
    return g


def wilder_rsi(c, p=14):
    d = c.diff()
    ag = d.clip(lower=0).ewm(alpha=1/p, min_periods=p, adjust=False).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1/p, min_periods=p, adjust=False).mean()
    return 100 - 100/(1 + ag/al.replace(0, np.nan))


def atr(g, p=14):
    pc = g["close"].shift(1)
    tr = pd.concat([g["high"]-g["low"], (g["high"]-pc).abs(), (g["low"]-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/p, min_periods=p, adjust=False).mean()


def adx(g, p=14):
    up = g["high"].diff(); dn = -g["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(g, p)
    pdi = 100 * pd.Series(plus, index=g.index).ewm(alpha=1/p, adjust=False).mean() / a
    mdi = 100 * pd.Series(minus, index=g.index).ewm(alpha=1/p, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/p, adjust=False).mean()


def signals(g):
    c = g["close"]
    sma = {p: c.rolling(p).mean() for p in (20, 50, 100, 200)}
    ema = {p: c.ewm(span=p, adjust=False, min_periods=p).mean() for p in (12, 20, 26, 50, 100, 200)}
    rsi = wilder_rsi(c, 14)
    macd = ema[12] - ema[26]; macd_sig = macd.ewm(span=9, adjust=False).mean()
    std20 = c.rolling(20).std(); bb_u = sma[20] + 2*std20; bb_l = sma[20] - 2*std20
    don_hi = g["high"].rolling(20).max().shift(1); don_lo = g["low"].rolling(20).min().shift(1)
    don_hi55 = g["high"].rolling(55).max().shift(1); don_lo20 = g["low"].rolling(20).min().shift(1)
    lo14 = c.rolling(14).min(); hi14 = c.rolling(14).max()
    stoch = 100 * (c - lo14) / (hi14 - lo14).replace(0, np.nan)
    adx14 = adx(g, 14)
    roc90 = c / c.shift(90) - 1
    roc30 = c / c.shift(30) - 1

    S = {}  # name -> long/flat boolean position (NaN where undefined)
    # ── Trend / moving-average ──
    S["SMA50  px>sma"]      = c > sma[50]
    S["SMA100 px>sma"]      = c > sma[100]
    S["SMA200 px>sma"]      = c > sma[200]
    S["EMA20>EMA50"]        = ema[20] > ema[50]
    S["EMA50>EMA200(golden)"] = ema[50] > ema[200]
    S["EMA12>EMA26"]        = ema[12] > ema[26]
    # ── MACD ──
    S["MACD>signal"]        = macd > macd_sig
    S["MACD hist>0 rising"] = (macd - macd_sig) > 0
    # ── Momentum / ROC ──
    S["ROC30>0"]            = roc30 > 0
    S["ROC90>0(3mo mom)"]   = roc90 > 0
    S["RSI14>50 (mom)"]     = rsi > 50
    # ── Breakout / price action ──
    S["Donchian20 breakout"] = donchian(c, don_hi, don_lo)
    S["Donchian55/20 turtle"] = donchian(c, don_hi55, don_lo20)
    # ── Mean reversion (oscillators) ──
    S["RSI14<30 revert"]    = hysteresis(rsi, enter_below=30, exit_above=55)
    S["Stoch<20 revert"]    = hysteresis(stoch, enter_below=20, exit_above=80)
    S["BB lower revert"]    = revert_band(c, bb_l, sma[20])
    S["BB upper breakout"]  = c > bb_u
    # ── Filtered / combined ──
    S["SMA200 & ADX>20"]    = (c > sma[200]) & (adx14 > 20)
    S["SMA200 & RSI>50"]    = (c > sma[200]) & (rsi > 50)
    S["EMA50>200 & MACD>sig"] = (ema[50] > ema[200]) & (macd > macd_sig)
    S["momo: px>SMA200 & RSI>70(7d)"] = (c > sma[200]) & (rsi > 70).rolling(7).max().astype(bool)
    return S


def donchian(c, hi, lo):
    pos = pd.Series(np.nan, index=c.index)
    pos[c > hi] = 1.0
    pos[c < lo] = 0.0
    return pos.ffill().fillna(0).astype(bool)


def hysteresis(ind, enter_below, exit_above):
    pos = pd.Series(np.nan, index=ind.index)
    pos[ind < enter_below] = 1.0
    pos[ind > exit_above] = 0.0
    return pos.ffill().fillna(0).astype(bool)


def revert_band(c, lower, mid):
    pos = pd.Series(np.nan, index=c.index)
    pos[c < lower] = 1.0
    pos[c > mid] = 0.0
    return pos.ffill().fillna(0).astype(bool)


def metrics(pos, ret, stance):
    p = pos.astype(float)
    if stance == "LS":
        p = p.replace(0.0, -1.0)
    held = p.shift(1).fillna(0.0)
    flips = held.diff().abs().fillna(0.0)
    sr = held * ret - COST * flips
    eq = (1 + sr).cumprod()
    yrs = len(sr) / ANN
    cagr = eq.iloc[-1] ** (1/yrs) - 1 if eq.iloc[-1] > 0 else -1.0
    sharpe = sr.mean() / sr.std() * np.sqrt(ANN) if sr.std() > 0 else 0.0
    dd = (eq / eq.cummax() - 1).min()
    expo = (held != 0).mean()
    nflip = int((flips > 0).sum())
    return dict(cagr=cagr, sharpe=sharpe, dd=dd, expo=expo, nflip=nflip, sr=sr, eq=eq)


def half_cagr(sr, mask):
    s = sr[mask]; eq = (1 + s).cumprod()
    yrs = len(s) / ANN
    return eq.iloc[-1] ** (1/yrs) - 1 if (len(s) and eq.iloc[-1] > 0) else -1.0


if __name__ == "__main__":
    g = load_daily()
    ret = g["close"].pct_change().fillna(0.0)
    mid = g.index[len(g)//2]
    is_m = g.index < mid; oos_m = g.index >= mid
    bh_cagr = (g["close"].iloc[-1]/g["close"].iloc[0]) ** (ANN/len(g)) - 1
    bh_sh = ret.mean()/ret.std()*np.sqrt(ANN)
    bh_dd = (g["close"]/g["close"].cummax()-1).min()
    print(f"Daily BTC {g.index[0].date()}->{g.index[-1].date()}  N={len(g)}  IS/OOS@{mid.date()}")
    print(f"BUY & HOLD: CAGR {bh_cagr*100:6.1f}%  Sharpe {bh_sh:4.2f}  maxDD {bh_dd*100:5.0f}%\n")
    S = signals(g)
    for stance in ("LF", "LS"):
        rows = []
        for name, pos in S.items():
            m = metrics(pos.reindex(g.index).fillna(False), ret, stance)
            rows.append((name, m, half_cagr(m["sr"], is_m), half_cagr(m["sr"], oos_m)))
        rows.sort(key=lambda r: r[1]["sharpe"], reverse=True)
        print(f"===== stance={stance} ({'long/flat spot' if stance=='LF' else 'long/short'}) — sorted by net Sharpe =====")
        print(f"  {'indicator':30s} {'CAGR':>7} {'Shrp':>5} {'maxDD':>6} {'expo':>5} {'flips':>5} {'IS-CAGR':>8} {'OOS-CAGR':>9}")
        for name, m, isc, oosc in rows:
            flag = "  <<" if (isc > 0 and oosc > 0 and m["sharpe"] > bh_sh) else ""
            print(f"  {name:30s} {m['cagr']*100:6.1f}% {m['sharpe']:5.2f} {m['dd']*100:5.0f}% "
                  f"{m['expo']*100:4.0f}% {m['nflip']:5d} {isc*100:7.1f}% {oosc*100:8.1f}%{flag}")
        print()
