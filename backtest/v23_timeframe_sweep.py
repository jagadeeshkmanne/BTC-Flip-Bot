#!/usr/bin/env python3
"""v23_timeframe_sweep.py — backtest the v2.3 regime router across 4 timeframe
combinations (regime_tf / exec_tf):  1d/4h, 4h/1h, 1h/15m, 15m/5m.

Faithful to the live v2.3 logic:
  - regime_tf: ADX14 -> trend (>=25) / range (<20) / flat (between); EMA20/50 for
    trend direction + |gap| firmness.
  - exec_tf: RSI9 entries; with-trend in trend regime, counter-trend in range regime.
  - 2-leg DCA at adverse spacing, adaptive TP (single/DCA), SL from worst entry,
    hard time-stop. 5x, honest fills (next-bar open +/- slip), taker fees.

TIMEFRAME SCALING (the point of the sweep): a 0.5% TP fits 5m bars but is far too
tight for 4h bars. Exits scale ~sqrt(time) vs the 5m baseline (approximating how
BTC's per-bar range grows). Baseline (5m): TP 0.5/0.25%, SL 0.6%, DCA 0.5%, gap
0.20%. Documented assumption — not a tuned optimum.

Honest engine (FINDINGS checklist): signals on CLOSED bars, fill next bar open;
SL filled at worse(stop, open); same-bar SL+TP -> SL; same-bar DCA+TP -> defer TP.
"""
from __future__ import annotations
import os, sys, math
import numpy as np
import pandas as pd

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "cache")
FEE, SLIP = 0.00055, 0.0002
LEV = 5.0
RSI_OS, RSI_OB = 30, 70
TREND_ADX, RANGE_ADX = 25.0, 20.0
TIME_STOP_BARS = 72
BE_WAIT = 6
TF_MIN = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
COMBOS = [("1d", "4h"), ("4h", "1h"), ("1h", "15m"), ("15m", "5m")]


from functools import lru_cache
@lru_cache(maxsize=8)
def load(tf):
    df = pd.read_csv(f"{CACHE}/BTCUSDT_{tf}.csv", parse_dates=["timestamp"])
    return df.set_index("timestamp").sort_index()


def rsi(c, n=9):
    d = c.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
    ag = g.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    al = l.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    return 100 - 100/(1 + ag/al.replace(0, np.nan))


def adx_dir_gap(df, n=14):
    up = df.high.diff(); dn = -df.low.diff()
    plus = pd.Series(((up>dn)&(up>0))*up, index=df.index).fillna(0)
    minus = pd.Series(((dn>up)&(dn>0))*dn, index=df.index).fillna(0)
    pc = df.close.shift(1)
    tr = pd.concat([df.high-df.low,(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n,adjust=False).mean()
    pdi = 100*plus.ewm(alpha=1/n,adjust=False).mean()/atr
    mdi = 100*minus.ewm(alpha=1/n,adjust=False).mean()/atr
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    adx = dx.ewm(alpha=1/n,adjust=False).mean()
    ef = df.close.ewm(span=20,adjust=False).mean()
    es = df.close.ewm(span=50,adjust=False).mean()
    return pd.DataFrame({"adx":adx, "trend":np.where(ef>es,"UP","DOWN"),
                         "gap":(ef-es)/es*100}, index=df.index)


def run_combo(regime_tf, exec_tf, lev=LEV, rsi_os=RSI_OS, rsi_ob=RSI_OB,
              gap_mult=1.0, sl_mult=1.0, tp_mult=1.0, htf_rsi=None, t0=None, t1=None):
    F = math.sqrt(TF_MIN[exec_tf] / 5.0)          # exec exits scale vs 5m
    tp_s, tp_d = 0.005*F*tp_mult, 0.0025*F*tp_mult
    sl_pct, dca_sp = 0.006*F*sl_mult, 0.005*F
    Fg = math.sqrt(TF_MIN[regime_tf] / 15.0)      # gap firmness scales vs 15m
    gap_min = 0.20 * Fg * gap_mult
    bar_ms = TF_MIN[exec_tf]

    ex = load(exec_tf); rg = load(regime_tf)
    ex = ex[ex.index >= ex.index[0]].copy()
    ex["rsi"] = rsi(ex.close)
    reg = adx_dir_gap(rg)
    reg["hrsi"] = rsi(rg.close)                   # HTF RSI (regime-tf RSI) for confirmation
    # align: regime known at its bar CLOSE -> shift to close time, asof-merge
    reg = reg.copy()
    reg["rclose"] = rg.index + pd.Timedelta(minutes=TF_MIN[regime_tf])
    reg = reg.dropna(subset=["adx"]).sort_values("rclose")
    left = pd.DataFrame({"eclose": ex.index + pd.Timedelta(minutes=TF_MIN[exec_tf])})
    aligned = pd.merge_asof(left.sort_values("eclose"), reg[["rclose","adx","trend","gap","hrsi"]],
                            left_on="eclose", right_on="rclose", direction="backward")
    for col in ("adx","trend","gap","hrsi"):
        ex[col] = aligned[col].values
    if t0 is not None: ex = ex[ex.index >= t0]
    if t1 is not None: ex = ex[ex.index < t1]

    o=ex.open.values; h=ex.high.values; l=ex.low.values; c=ex.close.values
    rs=ex.rsi.values; adx=ex.adx.values; tr=ex.trend.values; gp=ex.gap.values; hr=ex.hrsi.values
    n=len(ex)
    bal=5000.0; eq_curve=[]; trades=[]
    pos=None  # dict
    for i in range(60, n-1):
        # ---- manage open position on bar i ----
        if pos:
            side=pos["side"]; sgn=1 if side=="LONG" else -1
            # DCA trigger (adverse beyond worst)
            if pos["filled"]<2:
                trig = pos["worst"]*(1-dca_sp) if side=="LONG" else pos["worst"]*(1+dca_sp)
                hit = (l[i]<=trig) if side=="LONG" else (h[i]>=trig)
                if hit:
                    q=pos["qty1"]; pos["entries"].append((trig,q)); pos["filled"]=2
                    pos["worst"]=min(pos["worst"],trig) if side=="LONG" else max(pos["worst"],trig)
                    pos["qty"]+=q; pos["l2_i"]=i
                    bal-=trig*q*FEE
            avg=sum(p*q for p,q in pos["entries"])/pos["qty"]
            tp_pct = tp_d if pos["filled"]>=2 else tp_s
            tp = avg*(1+sgn*tp_pct)
            # SL: from worst, or BE after DCA+wait
            if pos["filled"]>=2 and pos.get("l2_i") is not None and (i-pos["l2_i"])>=BE_WAIT:
                slp = avg
            else:
                slp = pos["worst"]*(1-sgn*sl_pct)
            hit_sl = (l[i]<=slp) if side=="LONG" else (h[i]>=slp)
            hit_tp = (h[i]>=tp) if side=="LONG" else (l[i]<=tp)
            exit_px=None
            if hit_sl:                                   # pessimistic: SL first
                exit_px = min(slp,o[i]) if side=="LONG" else max(slp,o[i])
            elif hit_tp and not (pos.get("l2_i")==i):    # defer TP on the DCA bar
                exit_px = tp
            elif i-pos["i0"]>=TIME_STOP_BARS:
                exit_px = c[i]
            if exit_px is not None:
                fill=exit_px*(1-sgn*SLIP)
                gross=sgn*(fill-avg)*pos["qty"]
                net=gross - fill*pos["qty"]*FEE - pos["efee"]
                bal+=net
                trades.append(net/pos["margin"])
                pos=None
        # ---- mark equity ----
        eq = bal + (sgn*(c[i]-sum(p*q for p,q in pos["entries"])/pos["qty"])*pos["qty"] if pos else 0)
        eq_curve.append(eq)
        # ---- entry decision on closed bar i -> fill i+1 open ----
        if pos is None and not np.isnan(rs[i]) and not np.isnan(adx[i]) and tr[i] is not None and not np.isnan(gp[i]):
            regime = "trend" if adx[i]>=TREND_ADX else ("range" if adx[i]<RANGE_ADX else None)
            if regime and abs(gp[i])>=gap_min:
                sig=None
                if rs[i]<=rsi_os: sig="LONG"
                elif rs[i]>=rsi_ob: sig="SHORT"
                if sig and regime=="trend":              # with-trend: direction must match
                    if (sig=="LONG")!=(tr[i]=="UP"): sig=None
                if sig and htf_rsi is not None and not np.isnan(hr[i]):   # HTF RSI confirm
                    if sig=="LONG" and hr[i] > htf_rsi: sig=None
                    elif sig=="SHORT" and hr[i] < (100-htf_rsi): sig=None
                # range/counter-trend: any direction OK
                if sig:
                    sgn=1 if sig=="LONG" else -1
                    entry=o[i+1]*(1+sgn*SLIP)
                    margin=bal*0.95/2  # per-leg margin (2 legs planned)
                    notional=margin*lev; q=notional/entry; efee=notional*FEE
                    bal-=efee
                    pos={"side":sig,"entries":[(entry,q)],"qty":q,"qty1":q,
                         "worst":entry,"filled":1,"i0":i+1,"l2_i":None,
                         "margin":margin,"efee":efee}
    eq=np.array(eq_curve)
    if len(eq)<2 or not trades:
        return dict(combo=f"{regime_tf}/{exec_tf}", trades=0)
    a=np.array(trades)
    peak=np.maximum.accumulate(eq); dd=(eq/peak-1).min()
    wins=a[a>0]; losses=a[a<=0]
    pf = wins.sum()/abs(losses.sum()) if len(losses) and losses.sum()!=0 else float("inf")
    yrs=(ex.index[-1]-ex.index[60]).days/365.25
    return dict(combo=f"{regime_tf}/{exec_tf}", trades=len(a), win=(a>0).mean()*100,
                pf=pf, net=(eq[-1]/5000-1)*100, dd=dd*100,
                tp=f"{tp_s*100:.2f}/{tp_d*100:.2f}", sl=f"{sl_pct*100:.2f}",
                gap=f"{gap_min:.2f}", span=f"{ex.index[60].date()}->{ex.index[-1].date()}", yrs=yrs)


def main():
    print(f"{'regime/exec':<12}{'span':<26}{'trades':>7}{'win%':>6}{'PF':>6}"
          f"{'net%':>9}{'maxDD':>8}  {'TP%':>11}{'SL%':>6}{'gap%':>6}")
    print("-"*100)
    for rtf, etf in COMBOS:
        r=run_combo(rtf, etf)
        if r.get("trades",0)==0:
            print(f"{r['combo']:<12}(no trades)"); continue
        print(f"{r['combo']:<12}{r['span']:<26}{r['trades']:>7}{r['win']:>6.0f}{r['pf']:>6.2f}"
              f"{r['net']:>+9.0f}{r['dd']:>+8.0f}  {r['tp']:>11}{r['sl']:>6}{r['gap']:>6}")
    print("\nNote: exits scaled ~sqrt(time) vs 5m baseline (assumption, not tuned). "
          "5x, honest fills+fees, 2-leg DCA. Relative TF ranking is the signal.")


if __name__ == "__main__":
    main()
