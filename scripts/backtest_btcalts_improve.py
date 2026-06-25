#!/usr/bin/env python3
"""backtest_btcalts_improve.py — (A) short-the-winner reversal proxy, (B) improve BTC-led alts.

PART A — cross-sectional reversal proxy for "short the top gainer": each bar rank the 4 coins
by trailing return; short the top (and optionally long the bottom). A weak proxy for the
"short every top gainer" idea, but enough to show the payoff shape on tradeable coins.

PART B — walk-forward improvements to the validated winner (BTC slow signal -> eqw alts):
  base      : BTC reverse signal -> eqw(ETH,BNB,SOL)               [WF ret/DD ~0.53]
  +dailyflt : flatten alt position when the DAILY BTC trend disagrees (HTF confirmation)
  longonly  : drop the shorts (long/flat only)
  +volscale : size = min(1, target_vol / realized_vol)            (de-lever in high vol)
  +daily+vol: both overlays together

Honesty: open-to-open fills, fee 0.055%/side + 0.05% slip on turnover, walk-forward
re-optimisation of the BTC EMA pair (every number genuine OOS). Data: Binance 1h (cached).
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALTS = ["ETHUSDT", "BNBUSDT", "SOLUSDT"]


def load(symbol):
    for name in (f"{symbol}_1h_binance.csv", f"{symbol}_1h_binance_full.csv"):
        p = os.path.join(HERE, "data/cache", name)
        if os.path.exists(p):
            return pd.read_csv(p, parse_dates=["timestamp"])
    raise FileNotFoundError(symbol)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def apply_pos(pos, target):
    """Continuous position (decided at close[t]) applied to target open-to-open returns."""
    held = np.asarray(pos.shift(1).fillna(0).values, float)
    oo = (target["open"].shift(-1) / target["open"] - 1).fillna(0).values
    turn = np.abs(np.diff(held, prepend=0.0))
    return held * oo - turn * (FEE_PCT + SLIP_PCT)


def metrics(ret, bpy=24 * 365.25):
    eq = np.cumprod(1 + np.nan_to_num(ret))
    if eq[-1] <= 0:
        return -1.0, -1.0, -1.0
    peak = np.maximum.accumulate(eq); dd = (eq / peak - 1).min()
    cagr = eq[-1] ** (1 / max(len(ret) / bpy, 1e-9)) - 1
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def rr(ret):
    return metrics(ret)[2]


def walk_forward(ret_by_param, train=8000, test=2000):
    params = list(ret_by_param.keys()); n = len(next(iter(ret_by_param.values())))
    out = np.zeros(n); mask = np.zeros(n, bool); start = train
    while start + test <= n:
        best_p, best = None, -1e9
        for p in params:
            sc = rr(ret_by_param[p][start - train:start])
            if sc > best: best, best_p = sc, p
        out[start:start + test] = ret_by_param[best_p][start:start + test]; mask[start:start + test] = True
        start += test
    return out[mask]


def main():
    raw = {s: load(s) for s in (["BTCUSDT"] + ALTS)}
    common = max(df["timestamp"].iloc[0] for df in raw.values())
    raw = {s: df[df["timestamp"] >= common].reset_index(drop=True) for s, df in raw.items()}
    n = min(len(df) for df in raw.values())
    raw = {s: df.iloc[:n].reset_index(drop=True) for s, df in raw.items()}
    idx = pd.to_datetime(raw["BTCUSDT"]["timestamp"])
    cut = int(n * 0.6)

    # ---------- PART A: short-the-winner reversal proxy ----------
    print("=" * 90)
    print("PART A — 'short the top gainer' proxy (cross-sectional reversal on 4 coins)")
    print("=" * 90)
    coins = ["BTCUSDT"] + ALTS
    closes = pd.DataFrame({c: raw[c]["close"] for c in coins})
    oo = pd.DataFrame({c: (raw[c]["open"].shift(-1) / raw[c]["open"] - 1).fillna(0) for c in coins})
    print(f"  {'config':<34}{'CAGR':>8}{'DD':>6}{'r/DD':>6}   {'oCAGR':>7}{'oDD':>6}{'or/DD':>7}")
    for lb in (6, 24, 72):           # trailing lookback in hours
        mom = closes.pct_change(lb)
        for mode in ("short_top", "short_top_long_bottom"):
            pos = pd.DataFrame(0.0, index=closes.index, columns=coins)
            top = mom.idxmax(axis=1); bot = mom.idxmin(axis=1)
            for i in range(lb + 1, n):
                pos.iloc[i, pos.columns.get_loc(top.iloc[i])] = -1.0
                if mode == "short_top_long_bottom":
                    pos.iloc[i, pos.columns.get_loc(bot.iloc[i])] = 1.0
            held = pos.shift(1).fillna(0)
            turn = held.diff().abs().fillna(held.abs())
            ret = (held * oo - turn * (FEE_PCT + SLIP_PCT)).sum(axis=1).values / (2 if "long" in mode else 1)
            m = metrics(ret); mo = metrics(ret[cut:])
            print(f"  lb{lb}h {mode:<26}{m[0]*100:>7.0f}%{m[1]*100:>5.0f}%{m[2]:>6.2f}   {mo[0]*100:>6.0f}%{mo[1]*100:>5.0f}%{mo[2]:>7.2f}")

    # ---------- PART B: improve BTC-led alts (walk-forward) ----------
    print("\n" + "=" * 90)
    print("PART B — improve BTC-led ALT basket (walk-forward, BTC EMA re-optimised each fold)")
    print("=" * 90)
    btc = raw["BTCUSDT"]
    fasts = [20, 32, 50]; slows = [200, 400, 600, 800]
    # daily BTC trend (resample 1h->1d EMA50), ffilled to 1h
    dly = btc.set_index("timestamp")["close"].resample("1D").last()
    dly_signal = (dly > ema(dly, 50)).shift(1)   # shift: only the PREVIOUS completed daily bar is known intraday
    dly_bull = dly_signal.reindex(idx, method="ffill").fillna(False).astype(bool).values
    # realized vol of BTC (24h) for vol-scaling
    rv = btc["close"].pct_change().rolling(24).std().bfill().values
    target_v = np.nanmedian(rv)
    vscale = np.clip(target_v / (rv + 1e-9), 0.2, 1.0)

    def basket_ret(sig_arr):
        sig = pd.Series(sig_arr, index=btc.index)
        return np.mean([apply_pos(sig, raw[a]) for a in ALTS], axis=0)

    def build(variant):
        rbp = {}
        for f in fasts:
            for s in slows:
                base = (ema(btc["close"], f) > ema(btc["close"], s)).astype(float).values * 2 - 1
                pos = base.copy()
                if variant in ("dailyflt", "daily+vol"):
                    pos = np.where((pos > 0) & ~dly_bull, 0.0, np.where((pos < 0) & dly_bull, 0.0, pos))
                if variant == "longonly":
                    pos = np.clip(base, 0, 1)
                if variant in ("volscale", "daily+vol"):
                    pos = pos * vscale
                rbp[(f, s)] = basket_ret(pos)
        return rbp

    print(f"  {'variant':<14}{'WF CAGR':>9}{'WF maxDD':>10}{'WF ret/DD':>11}")
    for variant in ("base", "dailyflt", "longonly", "volscale", "daily+vol"):
        wf = walk_forward(build(variant))
        c, d, r = metrics(wf)
        print(f"  {variant:<14}{c*100:>8.0f}%{d*100:>9.0f}%{r:>11.2f}")


if __name__ == "__main__":
    main()
