#!/usr/bin/env python3
"""backtest_v3_recon.py — faithful-as-possible reconstruction of BTC Flip Bot V3, run on 4H.

IMPORTANT about "1H code on a 4H chart": all the chart-TF indicators (RSI21, MACD, ATR20,
volume, engulfing) recompute on 4H candles. AND the Pine compares hour-named inputs to
bar_index (e.g. `bar_index - lastSlBar < 24`), so on a 4H chart "24h" cooldown = 24 BARS = 96h,
"168h" DD-halt = 168 bars = 28 days, etc. So the time-based logic is 4x longer in real time.
This reconstruction replicates that (bar-counted). Commission 0.04%/side + slippage, 2x sizing.
Won't match TradingView to the dollar (Pine fill semantics differ) — used to judge if the EDGE
is real (walk-forward / remove-best-trades), not to reproduce a screenshot.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

COMM=0.0004; SLIP=0.0003; LEV=2.0; CAP0=5000.0


def macd(c):
    ln=bt.ema(c,12)-bt.ema(c,26); return ln, bt.ema(ln,9)


def load(tf):
    raw=pd.read_csv(os.path.join(bt.CACHE,"BTCUSDT_1h_binance_volume.csv"),parse_dates=["timestamp"])
    if tf=="4h":
        df=raw.set_index("timestamp").resample("4h").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna().reset_index()
    else:
        df=raw.copy()
    dd=raw.set_index("timestamp").resample("1D").agg({"close":"last"}).dropna().reset_index()
    dd["bull"]=(dd["close"]>bt.ema(dd["close"],50)).shift(1)
    ts=pd.to_datetime(df["timestamp"])
    df["d_bull"]=pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
        dd.assign(ts=pd.to_datetime(dd["timestamp"]))[["ts","bull"]].sort_values("ts"),
        on="ts",direction="backward")["bull"].fillna(False).astype(bool).values
    # 4h-confirm RSI(14): on a 4h chart this is the prior 4h bar's rsi14
    h4=raw.set_index("timestamp").resample("4h").agg({"close":"last"}).dropna().reset_index()
    h4["r"]=bt.rsi(h4["close"],14).shift(1)
    df["h4r"]=pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
        h4.assign(ts=pd.to_datetime(h4["timestamp"]))[["ts","r"]].sort_values("ts"),
        on="ts",direction="backward")["r"].fillna(50).values
    return df


def run(df,P,use_flip=True):
    o=df["open"].values;h=df["high"].values;l=df["low"].values;c=df["close"].values;v=df["volume"].values
    rsi=bt.rsi(df["close"],P["rsi_len"]).values
    ml,ms=macd(df["close"]);ml=ml.values;ms=ms.values
    atr=bt.atr(df,20).values;atrma=pd.Series(atr).rolling(50).mean().values
    vsma=pd.Series(v).rolling(20).mean().values
    db=df["d_bull"].values;h4r=df["h4r"].values;body=np.abs(c-o)
    n=len(df);eq=CAP0;peak=CAP0;halt=-1
    pos=0;qty=0.0;entry=0.0;sl=0.0;partial=False;pyr=False;initsl=0.0;isflip=False;flipbar=-1
    lls=-9999;lss=-9999;lex=-9999;pend=0;pbar=-1;pref=0.0
    ec=np.full(n,CAP0);trades=[]

    def closeit(px):
        nonlocal eq,pos,qty,partial,pyr,isflip
        pnl=qty*(px-entry)-abs(qty)*px*(COMM+SLIP);eq+=pnl;trades.append(pnl)
        pos=0;qty=0.0;partial=False;pyr=False;isflip=False

    for i in range(60,n-1):
        px=c[i];peak=max(peak,eq)
        if (eq-peak)/peak<=-P["dd_halt"] and pos==0:
            halt=i+P["halt_bars"];peak=eq
        halted=i<halt
        if pos!=0:
            slhit=(l[i]<=sl) if pos>0 else (h[i]>=sl)
            if slhit:
                wasflip=isflip;wlong=pos>0;closeit(sl);lex=i
                if wlong: lls=i
                else: lss=i
                if use_flip and not wasflip:
                    pend=(-1 if wlong else 1);pbar=i;pref=sl
            elif isflip and (i-flipbar)>=P["flip_ts"]:
                closeit(px);lex=i
            else:
                R=((px-entry)/initsl) if pos>0 else ((entry-px)/initsl)
                if not partial and initsl>0 and R>=P["tp_r"]:
                    cq=qty*0.15;eq+=cq*(px-entry)-abs(cq)*px*(COMM+SLIP);qty-=cq;partial=True
                    sl=entry*(1+0.001) if pos>0 else entry*(1-0.001)
                if pos!=0 and not pyr and initsl>0 and R>=P["pyr_r"]:
                    add=(eq*LEV*0.5)/px*(1 if pos>0 else -1);eq-=abs(add)*px*(COMM+SLIP);qty+=add;pyr=True
                    sl=entry+0.5*initsl if pos>0 else entry-0.5*initsl
        ec[i]=eq if pos==0 else eq+qty*(c[i]-entry)
        bel=c[i-1]<o[i-1] and c[i]>o[i] and c[i]>=o[i-1] and o[i]<=c[i-1] and body[i]>body[i-1]*P["eng"]
        bes=c[i-1]>o[i-1] and c[i]<o[i] and o[i]>=c[i-1] and c[i]<=o[i-1] and body[i]>body[i-1]*P["eng"]
        hv=atr[i]>atrma[i];vok=v[i]>P["volr"]*vsma[i]
        lsig=rsi[i]>P["rsi_lmin"] and ml[i]>ms[i] and bel and hv and vok and db[i] and h4r[i]>50
        ssig=rsi[i]<P["rsi_smax"] and ml[i]<ms[i] and bes and hv and vok and (not db[i]) and h4r[i]<50
        if pos>0 and ssig: closeit(px);lex=i
        elif pos<0 and lsig: closeit(px);lex=i
        lcd=(i-lls)<P["cd"];scd=(i-lss)<P["cd"];xcd=(i-lex)<2
        # flip entry
        if pend!=0 and pos==0 and (i-pbar)>=1 and not halted and not xcd:
            if pend==1:
                swl=l[max(0,i-10):i+1].min();st=max(swl*(1-0.001),pref*(1-P["flip_cap"]))
                if px-st>0: qty=(eq*LEV)/px;entry=px;sl=st;pos=1;initsl=px-st;isflip=True;flipbar=i;eq-=eq*LEV*COMM
            else:
                swh=h[max(0,i-10):i+1].max();st=min(swh*(1+0.001),pref*(1+P["flip_cap"]))
                if st-px>0: qty=-(eq*LEV)/px;entry=px;sl=st;pos=-1;initsl=st-px;isflip=True;flipbar=i;eq-=eq*LEV*COMM
            pend=0
        # normal entry
        if pos==0 and pend==0 and not halted and not xcd:
            if lsig and not lcd:
                st=max(min(l[i],l[i-1])*(1-0.001),px*(1-P["sl_max"]))
                if px-st>0: qty=(eq*LEV)/px;entry=px*(1+SLIP);sl=st;pos=1;partial=pyr=False;initsl=px-st;eq-=eq*LEV*COMM
            elif ssig and not scd:
                st=min(max(h[i],h[i-1])*(1+0.001),px*(1+P["sl_max"]))
                if st-px>0: qty=-(eq*LEV)/px;entry=px*(1-SLIP);sl=st;pos=-1;partial=pyr=False;initsl=st-px;eq-=eq*LEV*COMM
    ec[n-1]=eq if pos==0 else eq+qty*(c[n-1]-entry)   # mark final bar
    eqs=pd.Series(ec,index=pd.to_datetime(df["timestamp"])).iloc[60:]
    return eqs,np.array(trades)


def stats(eqs,tr,label):
    eqs=eqs[eqs>0]
    if len(tr)==0: print(f"  {label}: no trades"); return
    yrs=max((eqs.index[-1]-eqs.index[0]).days/365.25,1e-9)
    cagr=(max(eqs.iloc[-1],1)/CAP0)**(1/yrs)-1
    dd=(eqs/eqs.cummax()-1).min()
    w=tr[tr>0];ls=tr[tr<=0];pf=w.sum()/abs(ls.sum()) if ls.sum()<0 else 9.99
    print(f"  {label}")
    print(f"    final ${eqs.iloc[-1]:,.0f} | total {(eqs.iloc[-1]/CAP0-1)*100:+.0f}% | CAGR {cagr*100:.0f}% | maxDD {dd*100:.0f}% | PF {pf:.2f} | trades {len(tr)} | win% {len(w)/len(tr)*100:.0f}")


def main():
    P=dict(rsi_len=21,rsi_lmin=45,rsi_smax=55,eng=1.0,volr=1.5,sl_max=0.025,tp_r=6.0,pyr_r=3.0,cd=24,dd_halt=0.25,halt_bars=168,flip_ts=24,flip_cap=0.015)
    print("="*88)
    print("V3 EXACT-PARAMS reconstruction (BTCUSDT) — same params, run on 4H chart vs 1H")
    print("="*88)
    for tf in ("1h","4h"):
        df=load(tf);eqs,tr=run(df,P)
        stats(eqs,tr,f"{tf.upper()} chart, full {df['timestamp'].iloc[0].date()}->{df['timestamp'].iloc[-1].date()}:")
        cut=eqs.index[int(len(eqs)*0.6)]
        eo=eqs[eqs.index>=cut];to=tr  # trades not date-split; report equity OOS slice
        oc=(max(eo.iloc[-1],1)/eo.iloc[0])**(1/max((eo.index[-1]-eo.index[0]).days/365.25,1e-9))-1
        odd=(eo/eo.cummax()-1).min()
        print(f"    OOS (last 40%, from {cut.date()}): CAGR {oc*100:.0f}% maxDD {odd*100:.0f}%")
        if len(tr)>5:
            idx=np.argsort(tr)[::-1][:3];kept=tr.copy();kept[idx]=0
            print(f"    remove top-3 trades: total {(np.cumsum(kept)[-1]+CAP0)/CAP0*100-100:+.0f}% (top3=${tr[idx].sum():,.0f} of ${tr.sum():,.0f} total profit)")
        print()


if __name__=="__main__":
    main()
