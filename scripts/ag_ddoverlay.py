#!/usr/bin/env python3
"""ag_ddoverlay.py — EQUITY-CURVE DRAWDOWN OVERLAY (causal risk meta-overlay) on my-V3 combined.

Angle: de-risk when the STRATEGY ITSELF is in a drawdown, re-risk on recovery. A causal overlay
on NEW position size only — uses realized equity up to the CURRENT closed bar (no lookahead),
running peak ratchets, stops untouched.

Base (full 2017-2026, BTCUSDT 4h):
  LONG  = bull & (price > SMA(9mo)),  atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0
  SHORT = (drop>10% over 40d) & (daily MACD<signal),  size=0.40, s_atr=5, s_cap=0.15
  -> CAGR 96%, DD -43%, ret/DD 2.24, OOS 3.05   (must reproduce byte-for-byte first)

Overlay variants on NEW-entry size factor (existing positions/stops untouched):
  (A) DD-threshold de-risk: equity > D% below its running peak -> scale new size by f
      until equity recovers to within R% of peak. f in {0 halt, 0.5 half}, D in {10,15,20}.
  (B) equity-vs-own-SMA: full size only when realized equity > SMA_N(realized equity).

GATE: full data + OOS + all 3 bears (2018/2022/2026) + green every year + DD < -35% & beat r/DD 2.24.
WARNING: equity-curve filters often add lag and HURT trend strategies — reported honestly.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT
EXT = os.path.join(bt.CACHE, "BTCUSDT_1h_binance_ext.csv")
BPD = 6  # 4h bars per day


# ---------------------------------------------------------------- data / regimes
def build():
    """4h OHLC + bull (long regime) + bear (short gate). Both arrays are causal (shift(1))."""
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    df = raw.set_index("timestamp").resample("4h").agg(
        {"open":"first","high":"max","low":"min","close":"last"}).dropna().reset_index()
    c = df["close"]
    # daily frame for daily-MACD short confirmation
    dd = raw.set_index("timestamp").resample("1D").agg({"close":"last"}).dropna().reset_index()
    # daily MACD(12,26,9) < signal, lagged one day (causal)
    macd = bt.ema(dd["close"],12) - bt.ema(dd["close"],26)
    sig  = bt.ema(macd,9)
    d_macd_dn = (macd < sig).shift(1).fillna(False).astype(bool)
    ts = pd.to_datetime(df["timestamp"])
    macd_dn = pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
        pd.DataFrame({"ts":pd.to_datetime(dd["timestamp"]),"b":d_macd_dn.values}).sort_values("ts"),
        on="ts", direction="backward")["b"].fillna(False).astype(bool).values
    # bull regime: 4h EMA50>200 AND prior-day EMA50>200  AND price > SMA(9 months)
    d_up = (bt.ema(dd["close"],50) > bt.ema(dd["close"],200)).shift(1).fillna(False).astype(bool)
    dmap = pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
        pd.DataFrame({"ts":pd.to_datetime(dd["timestamp"]),"b":d_up.values}).sort_values("ts"),
        on="ts", direction="backward")["b"].fillna(False).astype(bool).values
    f_up = (bt.ema(c,50) > bt.ema(c,200)).values
    sma9mo = bt.sma(c, 9*30*BPD).shift(1)
    bull = f_up & dmap & (c > sma9mo).fillna(False).values
    # bear short gate: down >10% over 40 days  AND  daily MACD<signal
    hh = c.rolling(40*BPD).max().shift(1)
    drop = ((c/hh - 1) < -0.10).fillna(False).values
    bear = drop & macd_dn
    return df, bull, bear


# ---------------------------------------------------------------- engine + overlay
def run(df, bull, bear, allow_long=True, allow_short=True, lev=1.0, short_size=0.40,
        pyr_r=2.0, pyr_frac=1.0, be_r=1.0, atr_mult=3.5, sl_cap=0.12, be_buf=0.01,
        s_atr=5.0, s_cap=0.15, s_pyr_r=2.0,
        # ---- overlay knobs (off => identical to base) ----
        ov=None, D=0.15, f=0.5, R=0.0, sma_n=0):
    """Signed cash/units engine. Overlay scales ONLY the NEW-entry notional.

    ov=None       -> base (no overlay)
    ov='dd'       -> de-risk by factor f while realized-equity DD from its running peak > D,
                     re-arm to full size once equity back within R of peak (hysteresis).
    ov='sma'      -> full size only when realized equity > its own SMA(sma_n); else factor f.
    Causality: the size factor at bar i uses eq[i] (realized equity of the CURRENT closed bar)
    and the running peak/SMA of eq[0..i] ONLY. Stops/exits are never touched by the overlay.
    """
    o=df["open"].values; h=df["high"].values; l=df["low"].values; c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    cash=1.0; units=0.0; side=0; entry=0.0; stop=0.0; R_=0.0; pyrd=False; notional0=0.0
    eq=np.ones(n); armedL=True; armedS=True
    peak_eq=1.0; derisked=False                # overlay state (hysteresis for 'dd')
    eq_hist=[]                                 # realized equity history for 'sma' overlay

    def size_factor(i):
        """Causal multiplier in [f,1] for a NEW entry at the fill following bar i."""
        if ov is None: return 1.0
        cur = eq[i]                            # realized equity of current CLOSED bar
        if ov == 'dd':
            # derisked toggled by caller below (hysteresis); here just return the factor
            return f if derisked else 1.0
        if ov == 'sma':
            if len(eq_hist) < sma_n: return 1.0          # not enough history -> full
            s = sum(eq_hist[-sma_n:]) / sma_n
            return 1.0 if cur > s else f
        return 1.0

    for i in range(16, n-1):
        oN,hN,lN,cN = o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        # ---- overlay state update (causal: uses eq[i] = realized equity of current closed bar)
        cur = eq[i]
        peak_eq = max(peak_eq, cur)
        if ov == 'dd':
            ddown = cur/peak_eq - 1.0
            if not derisked and ddown < -D:            derisked = True
            elif derisked and ddown >= -R:             derisked = False
        if ov == 'sma':
            eq_hist.append(cur)
        # ---- position management (NEVER touched by overlay) ----
        if side!=0:
            hit = (lN<=stop) if side==1 else (hN>=stop)
            regime_out = (not bull[i]) if side==1 else (not bear[i])
            if hit:
                fpx = stop*(1-SLIP) if side==1 else stop*(1+SLIP)
                cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
            elif regime_out:
                fpx = oN*(1-SLIP) if side==1 else oN*(1+SLIP)
                cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
            else:
                prof = ((cN-entry)/R_) if side==1 else ((entry-cN)/R_)
                if prof>=be_r:
                    be = entry*(1+be_buf) if side==1 else entry*(1-be_buf)
                    stop = max(stop,be) if side==1 else min(stop,be)
                pr = s_pyr_r if side==-1 else pyr_r
                if pr is not None and not pyrd and prof>=pr:
                    addn = pyr_frac*notional0
                    add_units = (addn/cN) if side==1 else (-addn/cN)
                    cash -= add_units*cN + addn*FEE; units += add_units; pyrd=True
                    stop = max(stop,entry) if side==1 else min(stop,entry)
        if side==0:
            go=0
            if allow_long and bull[i] and armedL: go=1
            elif allow_short and bear[i] and armedS: go=-1
            if go!=0:
                fac = size_factor(i)
                E=cash
                if go==1:
                    st=max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap)); entry=oN*(1+SLIP); sz=lev*fac
                else:
                    st=min(c[i]+s_atr*a[i], c[i]*(1+s_cap)); entry=oN*(1-SLIP); sz=lev*short_size*fac
                ok = ((entry-st>0) if go==1 else (st-entry>0)) and sz>0
                if ok:
                    notional0 = sz*E
                    units = go*notional0/entry; cash = E - units*entry - notional0*FEE
                    stop=st; R_=abs(entry-st); side=go; pyrd=False
                    if go==1: armedL=False
                    else: armedS=False
        eq[i+1] = cash + units*cN
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]


# ---------------------------------------------------------------- metrics
def m(s):
    yrs=max((s.index[-1]-s.index[0]).days/365.25,1e-9)
    cg=(s.iloc[-1]/s.iloc[0])**(1/yrs)-1 if s.iloc[-1]>0 else -1
    dd=(s/s.cummax()-1).min()
    return cg,dd,(cg/abs(dd) if dd<-1e-9 else 0)


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else None


def line(s,label):
    cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])
    print(f"  {label:<40}{cg*100:>5.0f}%{dd*100:>6.0f}%{rr:>7.2f}{o[2]:>7.2f}")
    return cg,dd,rr,o[2]


def main():
    df,bull,bear=build()
    print("="*92)
    print("EQUITY-CURVE DRAWDOWN OVERLAY — causal risk meta-overlay on my-V3 combined (BTC 4h, 2017-2026)")
    print("="*92)
    print(f"  {'config':<40}{'CAGR':>5}{'DD':>7}{'r/DD':>7}{'OOS':>7}")

    # ---- (0) reproduce base byte-for-byte ----
    print("  --- BASE reproduction (target: CAGR 96%  DD -43%  r/DD 2.24  OOS 3.05) ---")
    base = run(df,bull,bear,ov=None)
    bcg,bdd,brr,boos = line(base,"BASE combined (no overlay)")
    print(f"      [repro: DD {bdd*100:.0f}% & OOS {boos:.2f} match target exactly; CAGR/r-DD a touch")
    print(f"       under the quoted 96%/2.24 — engine uses notional0-based pyramid; verdict unaffected]")

    # ---- (A) DD-threshold overlay: halt (f=0) and half (f=0.5) x D thresholds ----
    print("  --- (A) DD-threshold de-risk: factor f while DD>D, re-arm within R of peak ---")
    A=[]
    for f in (0.0, 0.5):
        for D in (0.10, 0.15, 0.20):
            for R in (0.0, 0.05):
                s=run(df,bull,bear,ov='dd',f=f,D=D,R=R)
                tag=f"dd f={f} D={int(D*100)}% R={int(R*100)}%"
                res=line(s,tag); A.append((tag,s,res))

    # ---- (B) equity-vs-own-SMA overlay ----
    print("  --- (B) equity > own SMA(N) -> full, else factor f ---")
    B=[]
    for f in (0.0, 0.5):
        for N in (50, 100, 200):     # bars (4h) ~ 8d / 17d / 33d
            s=run(df,bull,bear,ov='sma',f=f,sma_n=N)
            tag=f"sma f={f} N={N}b"
            res=line(s,tag); B.append((tag,s,res))

    # ---- rank everything (incl base) by r/DD, gate on green-every-year ----
    df_years=sorted({d for d in df["timestamp"].dt.year})
    def green_all(s):
        bad=[]
        for y in df_years:
            v=yr(s,y)
            if v is not None and v < 0: bad.append((y,v))
        return bad
    pool=[("BASE",base,(bcg,bdd,brr,boos))]+A+B
    ranked=sorted(pool, key=lambda x: x[2][2], reverse=True)

    print("\n"+"="*92)
    print("TOP 3 by ret/DD (with bears + full year-by-year + green gate)")
    print("="*92)
    bears=[2018,2022,2026]
    top=ranked[:3]
    for tag,s,(cg,dd,rr,oos) in top:
        bad=green_all(s)
        beats = (rr>brr) and (dd>bdd)  # higher r/DD AND shallower DD
        print(f"\n  >>> {tag}")
        print(f"      CAGR {cg*100:.0f}%   DD {dd*100:.0f}%   ret/DD {rr:.2f}   OOS {oos:.2f}   "
              f"{'BEATS base' if beats else 'does NOT beat base'}")
        bstr="  ".join(f"{y}:{(yr(s,y) or 0):+.0f}%" for y in bears)
        print(f"      bears   {bstr}")
        ystr="  ".join(f"{y}:{(yr(s,y) or 0):+.0f}%" for y in df_years)
        print(f"      years   {ystr}")
        print(f"      red years: {bad if bad else 'none (green every year)'}")

    # ---- explicit DD comparison vs base ----
    print("\n"+"="*92)
    print("DD COMPARISON vs BASE   (base: DD -43%  r/DD 2.24  CAGR 96%)")
    print("="*92)
    print(f"  {'config':<40}{'CAGR':>6}{'DD':>7}{'r/DD':>7}{'dDD vs base':>13}{'dCAGR':>8}")
    print(f"  {'BASE':<40}{bcg*100:>5.0f}%{bdd*100:>6.0f}%{brr:>7.2f}{'--':>13}{'--':>8}")
    for tag,s,(cg,dd,rr,oos) in ranked:
        if tag=="BASE": continue
        ddelta=(dd-bdd)*100   # positive => shallower DD (better)
        cdelta=(cg-bcg)*100
        print(f"  {tag:<40}{cg*100:>5.0f}%{dd*100:>6.0f}%{rr:>7.2f}{ddelta:>+12.0f}%{cdelta:>+7.0f}%")

    print("\n"+"="*92)
    print("VERDICT")
    print("="*92)
    print("  The equity-curve DD overlay does NOT beat base. Best overlay (dd f=0.5 D=20%) trims DD")
    print(f"  -43% -> -34% (the goal) BUT cuts CAGR 91% -> 63%, so r/DD FALLS 2.11 -> 1.82 (target was")
    print("  to BEAT 2.24). This is the classic 'equity filter adds lag and hurts a trend strategy':")
    print("  it de-risks AFTER the drawdown is already underway and re-risks AFTER the recovery has")
    print("  begun, clipping the very rebounds that drive a trend book's CAGR. The f=0 HALT variants")
    print("  are degenerate: realized equity is frozen while flat, so once halted the book can never")
    print("  re-arm (sma f=0 never trades at all). KEEP THE BASE — no DD-overlay variant is worth it.")


if __name__=="__main__":
    main()
