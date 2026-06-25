#!/usr/bin/env python3
"""short_blend_engine.py — COPY of run_ec3 (config-D engine) where the SHORT trades a
CONSTANT-WEIGHT normalized BTC+ETH BASKET instead of a single instrument.

LONG stays 100% BTC, unchanged. The bear SIGNAL is still 100% BTC (drop>10% from 35d high
AND BTC daily MACD<sig). Only the thing being SHORTED changes.

Basket construction (causal, no lookahead):
  - load BTC + ETH 4h, align to common timestamps (same as engine).
  - normalize each asset's O/H/L/C by its OWN first common close -> btcN, ethN start at ~1.0.
  - basket_X[t] = w_btc*btcN_X[t] + w_eth*ethN_X[t]  for X in O/H/L/C, w_btc+w_eth=1.
  - basket ATR(14) computed from the basket OHLC (its own ATR).
At w_eth=1.0 (w_btc=0.0) the basket == pure normalized ETH, so this MUST reproduce config D.

Everything else (long pyramid/lock/parabolic, bear-depth sizing, FEE/SLIP, shift(1) signals,
next-bar fills, trailing short stop on basket lows) is byte-for-byte run_ec3.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_btclong_ethshort import load4h, dbull_map

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT; BPD = 6


def _basket_ohlc(btc, eth, w_btc, w_eth):
    """Return normalized-and-weighted basket O/H/L/C arrays + basket ATR(14)."""
    bn = btc["close"].iloc[0]; en = eth["close"].iloc[0]
    bo = btc["open"].values / bn; bh = btc["high"].values / bn
    bl = btc["low"].values / bn;  bc = btc["close"].values / bn
    eo = eth["open"].values / en; eh = eth["high"].values / en
    el = eth["low"].values / en;  ec = eth["close"].values / en
    o = w_btc*bo + w_eth*eo
    c = w_btc*bc + w_eth*ec
    # weighted normalized highs/lows; high>=close>=low preserved since it's a convex combo
    h = w_btc*bh + w_eth*eh
    l = w_btc*bl + w_eth*el
    bask = pd.DataFrame({"open": o, "high": h, "low": l, "close": c})
    a = bt.atr(bask, 14).values
    return o, h, l, c, a


def run_blend(w_btc=0.0, w_eth=1.0, long_lev=None, lock_frac=0.33, lock_r=6.0,
              mf=12, ms=26, msig=9, confirm="macd", macd_tf="1d", drop_pct=0.10, drop_look=35,
              atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0, be_buf=0.01,
              s_atr=6.0, s_cap=0.20,
              s_lev=1.0, size_lo=0.5, size_mid=1.0, size_hi=1.0,
              spyr_k=0.0, spyr_frac=0.5,
              strail_k=3.5, strail_arm_R=1.0,
              s_reentry=False):
    btc, btcd = load4h("BTCUSDT"); eth, ethd = load4h("ETHUSDT")
    common = pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    btc = btc[btc["timestamp"].isin(common)].reset_index(drop=True)
    eth = eth[eth["timestamp"].isin(common)].reset_index(drop=True)
    bc = btc["close"]; ec = eth["close"]
    # ----- SIGNAL: 100% BTC (unchanged from config D) -----
    bull = ((bt.ema(bc, 50) > bt.ema(bc, 200)).values & dbull_map(btc, btcd)
            & (bc > bt.sma(bc, 9*30*BPD).shift(1)).fillna(False).values)
    parab = (bc > 2.2*bt.sma(bc, 140*BPD).shift(1)).fillna(False).values
    ts4 = pd.to_datetime(btc["timestamp"])
    if macd_tf == "4h":
        ml4 = bt.ema(bc, mf) - bt.ema(bc, ms); sig4 = bt.ema(ml4, msig)
        macd_bear = (ml4 < sig4).shift(1).fillna(False).values
    else:
        mld = bt.ema(btcd["close"], mf) - bt.ema(btcd["close"], ms); sigd = bt.ema(mld, msig)
        dmb = (mld < sigd).shift(1).fillna(False)
        macd_bear = pd.merge_asof(pd.DataFrame({"ts": ts4}).sort_values("ts"),
            pd.DataFrame({"ts": pd.to_datetime(btcd["timestamp"]), "b": dmb.values}).sort_values("ts"),
            on="ts", direction="backward")["b"].fillna(False).astype(bool).values
    deb = (bt.ema(btcd["close"], 50) < bt.ema(btcd["close"], 200)).shift(1).fillna(False)
    ema_bear = pd.merge_asof(pd.DataFrame({"ts": ts4}).sort_values("ts"),
        pd.DataFrame({"ts": pd.to_datetime(btcd["timestamp"]), "b": deb.values}).sort_values("ts"),
        on="ts", direction="backward")["b"].fillna(False).astype(bool).values
    drop_arr = ((bc/bc.rolling(drop_look*BPD).max().shift(1)-1) < -drop_pct).fillna(False).values
    conf = {"macd": macd_bear, "ema": ema_bear, "both": macd_bear & ema_bear,
            "either": macd_bear | ema_bear, "none": np.ones(len(bc), bool)}[confirm]
    bear = drop_arr & conf
    bdd = (bc/bc.rolling(180*BPD).max().shift(1)-1).fillna(0).values
    ssize = np.where(bdd <= -0.30, size_hi, np.where(bdd <= -0.20, size_mid, size_lo))
    if long_lev is None: long_lev = np.ones(len(btc))
    # ----- LONG instrument: 100% BTC (unchanged) -----
    bo, bh, bl, bcv = [btc[k].values for k in ("open", "high", "low", "close")]
    ba = bt.atr(btc, 14).values
    # ----- SHORT instrument: normalized BTC+ETH basket -----
    so, sh, sl_, scv, sa = _basket_ohlc(btc, eth, w_btc, w_eth)
    n = len(btc); cash = 1.0; units = 0.0; side = 0; entry = stop = R = 0.0
    pyrd = parabd = lockd = False; notional0 = 0.0
    spyrd = False; lowS = np.inf
    armedL = armedS = True; eq = np.ones(n)
    def mpx(i): return bcv[i] if side == 1 else (scv[i] if side == -1 else bcv[i])
    for i in range(16, n-1):
        if not bull[i]: armedL = True
        if not bear[i]: armedS = True
        covered_this_bar = False
        if side == 1:
            lN = bl[i+1]; oN = bo[i+1]; cN = bcv[i+1]
            if lN <= stop:
                fpx = stop*(1-SLIP); cash += units*fpx-abs(units)*fpx*FEE; units = 0.0; side = 0; pyrd = parabd = lockd = False
            elif not bull[i]:
                fpx = oN*(1-SLIP); cash += units*fpx-abs(units)*fpx*FEE; units = 0.0; side = 0; pyrd = parabd = lockd = False
            else:
                prof = (cN-entry)/R
                if prof >= be_r: stop = max(stop, entry*(1+be_buf))
                if not pyrd and prof >= pyr_r:
                    addn = pyr_frac*notional0; au = addn/cN; cash -= au*cN+addn*FEE; units += au; pyrd = True; stop = max(stop, entry)
                if lock_frac > 0 and not lockd and prof >= lock_r:
                    fpx = oN*(1-SLIP); cl = lock_frac*units; cash += cl*fpx*(1-FEE); units -= cl; lockd = True
                if not parabd and parab[i]:
                    fpx = oN*(1-SLIP); cl = 0.5*units; cash += cl*fpx*(1-FEE); units -= cl; parabd = True
        elif side == -1:
            if sl_[i] < lowS: lowS = sl_[i]
            hN = sh[i+1]; oN = so[i+1]; cN = scv[i+1]
            tstop = stop
            if strail_k > 0:
                prof_c = (entry-scv[i])/R
                if prof_c >= strail_arm_R:
                    cand = lowS+strail_k*sa[i]
                    tstop = min(stop, cand)
            eff_stop = tstop
            if hN >= eff_stop:
                fpx = eff_stop*(1+SLIP); cash += units*fpx-abs(units)*fpx*FEE; units = 0.0; side = 0; covered_this_bar = True
                stop = eff_stop
            elif not bear[i]:
                fpx = oN*(1+SLIP); cash += units*fpx-abs(units)*fpx*FEE; units = 0.0; side = 0; covered_this_bar = True
            else:
                stop = eff_stop
                prof = (entry-cN)/R
                if prof >= be_r: stop = min(stop, entry*(1-be_buf))
                if spyr_k > 0 and not spyrd:
                    fall = (entry-cN)/R
                    if fall >= spyr_k:
                        addn = spyr_frac*notional0s; au = addn/cN
                        cash += au*cN-addn*FEE
                        units -= au; spyrd = True; stop = min(stop, entry)
        if side == 0:
            allowS = armedS or (s_reentry and not covered_this_bar)
            if bull[i] and armedL:
                E = cash; oN = bo[i+1]; st = max(bcv[i]-atr_mult*ba[i], bcv[i]*(1-sl_cap)); ent = oN*(1+SLIP)
                if ent-st > 0:
                    notional0 = E*long_lev[i]; units = notional0/ent; cash -= units*ent+notional0*FEE; entry = ent; stop = st; R = ent-st; side = 1; pyrd = parabd = lockd = False; armedL = False
            elif bear[i] and allowS:
                E = cash; oN = so[i+1]; st = min(scv[i]+s_atr*sa[i], scv[i]*(1+s_cap)); ent = oN*(1-SLIP)
                if st-ent > 0:
                    notional = ssize[i]*s_lev*E; units = -notional/ent; cash -= units*ent+notional*FEE
                    entry = ent; stop = st; R = st-ent; side = -1; armedS = False
                    notional0s = notional; spyrd = False; lowS = np.inf
        eq[i+1] = cash+units*mpx(i+1)
    return pd.Series(eq, index=pd.to_datetime(btc["timestamp"])).iloc[16:]
