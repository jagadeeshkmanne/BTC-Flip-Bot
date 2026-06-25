#!/usr/bin/env python3
"""backtest_btclong_ethshort.py — BTC for longs + ETH for shorts (two-asset engine).

Idea: BTC has the cleanest uptrends (best long); ETH crashes harder in bears (best short).
Run the V2 LONG on BTC and the bear-depth SHORT on ETH, on ONE shared account. Compare to
BTC-only V2 (long+short both BTC). Honest cash/units, mark = cash + btc_u*btc_px + eth_u*eth_px.
Short gate variants: ETH's own drop+MACD, or BTC's bear signal driving the ETH short.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_final import m

FEE=bt.FEE_PCT; SLIP=bt.SLIP_PCT; BPD=6


def load4h(sym):
    raw=pd.read_csv(os.path.join(bt.CACHE,f"{sym}_1h_binance_ext.csv"),parse_dates=["timestamp"])
    df=raw.set_index("timestamp").resample("4h").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna().reset_index()
    dd=raw.set_index("timestamp").resample("1D").agg({"close":"last"}).dropna().reset_index()
    return df, dd


def macd_bear_map(df, dd):
    ml=bt.ema(dd["close"],12)-bt.ema(dd["close"],26); sig=bt.ema(ml,9)
    b=(ml<sig).shift(1).fillna(False)
    ts=pd.to_datetime(df["timestamp"])
    return pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
        pd.DataFrame({"ts":pd.to_datetime(dd["timestamp"]),"b":b.values}).sort_values("ts"),
        on="ts",direction="backward")["b"].fillna(False).astype(bool).values


def dbull_map(df, dd):
    b=(bt.ema(dd["close"],50)>bt.ema(dd["close"],200)).shift(1).fillna(False)
    ts=pd.to_datetime(df["timestamp"])
    return pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
        pd.DataFrame({"ts":pd.to_datetime(dd["timestamp"]),"b":b.values}).sort_values("ts"),
        on="ts",direction="backward")["b"].fillna(False).astype(bool).values


def two_asset(btc, eth, bull, parab, bear, ssize,
              atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0, be_buf=0.01, s_atr=5.0, s_cap=0.15):
    bo,bh,bl,bc=[btc[k].values for k in ("open","high","low","close")]; ba=bt.atr(btc,14).values
    eo,eh,el,ec=[eth[k].values for k in ("open","high","low","close")]; ea=bt.atr(eth,14).values
    n=len(btc)
    cash=1.0; bu=0.0; eu=0.0
    lside=0; lentry=0.0; lstop=0.0; lR=0.0; pyrd=False; parabd=False; armedL=True
    sside=0; sentry=0.0; sstop=0.0; sR=0.0; armedS=True
    eq=np.ones(n)
    def equity(i): return cash + bu*bc[i] + eu*ec[i]
    for i in range(16,n-1):
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        # ---- manage BTC long ----
        if lside==1:
            lN=bl[i+1]; oN=bo[i+1]; cN=bc[i+1]
            if lN<=lstop:
                fpx=lstop*(1-SLIP); cash+=bu*fpx-abs(bu)*fpx*FEE; bu=0.0; lside=0; pyrd=False; parabd=False
            elif not bull[i]:
                fpx=oN*(1-SLIP); cash+=bu*fpx-abs(bu)*fpx*FEE; bu=0.0; lside=0; pyrd=False; parabd=False
            else:
                prof=(cN-lentry)/lR
                if prof>=be_r: lstop=max(lstop,lentry*(1+be_buf))
                if not pyrd and prof>=pyr_r:
                    E=equity(i); addn=pyr_frac*(E); au=addn/cN  # add ~equity-sized
                    # cap add notional to original notional0 analog: use pyr_frac * current long notional
                    addn=pyr_frac*(bu*cN); au=addn/cN
                    cash-=au*cN+addn*FEE; bu+=au; pyrd=True; lstop=max(lstop,lentry)
                if not parabd and parab[i]:
                    fpx=oN*(1-SLIP); cl=0.5*bu; cash+=cl*fpx*(1-FEE); bu-=cl; parabd=True
        # ---- manage ETH short ----
        if sside==-1:
            hN=eh[i+1]; oN=eo[i+1]; cN=ec[i+1]
            if hN>=sstop:
                fpx=sstop*(1+SLIP); cash+=eu*fpx-abs(eu)*fpx*FEE; eu=0.0; sside=0
            elif not bear[i]:
                fpx=oN*(1+SLIP); cash+=eu*fpx-abs(eu)*fpx*FEE; eu=0.0; sside=0
            else:
                prof=(sentry-cN)/sR
                if prof>=be_r: sstop=min(sstop,sentry*(1-be_buf))
        # ---- BTC long entry ----
        if lside==0 and bull[i] and armedL:
            E=equity(i); oN=bo[i+1]; st=max(bc[i]-atr_mult*ba[i], bc[i]*(1-sl_cap)); ent=oN*(1+SLIP)
            if ent-st>0:
                notional=E; bu=notional/ent; cash-=bu*ent+notional*FEE
                lentry=ent; lstop=st; lR=ent-st; lside=1; pyrd=False; parabd=False; armedL=False
        # ---- ETH short entry ----
        if sside==0 and bear[i] and armedS:
            E=equity(i); oN=eo[i+1]; st=min(ec[i]+s_atr*ea[i], ec[i]*(1+s_cap)); ent=oN*(1-SLIP)
            if st-ent>0:
                notional=ssize[i]*E; eu=-notional/ent; cash-=eu*ent+notional*FEE
                sentry=ent; sstop=st; sR=st-ent; sside=-1; armedS=False
        eq[i+1]=equity(i+1)
    return pd.Series(eq,index=pd.to_datetime(btc["timestamp"])).iloc[16:]


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else None


def main():
    btc,btcd=load4h("BTCUSDT"); eth,ethd=load4h("ETHUSDT")
    # align on common 4h timestamps
    common=pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    btc=btc[btc["timestamp"].isin(common)].reset_index(drop=True)
    eth=eth[eth["timestamp"].isin(common)].reset_index(drop=True)
    bc=btc["close"]; ec=eth["close"]
    # BTC long gate + parabolic
    bull=( (bt.ema(bc,50)>bt.ema(bc,200)).values & dbull_map(btc,btcd) & (bc>bt.sma(bc,9*30*BPD).shift(1)).fillna(False).values )
    parab=(bc>2.2*bt.sma(bc,140*BPD).shift(1)).fillna(False).values
    # ETH short gate (ETH's own drop + ETH daily MACD) + ETH bear-depth
    eth_bear=((ec/ec.rolling(40*BPD).max().shift(1)-1)<-0.10).fillna(False).values & macd_bear_map(eth,ethd)
    eth_ddh=(ec/ec.rolling(180*BPD).max().shift(1)-1).fillna(0).values
    eth_ss=np.where(eth_ddh<=-0.30,1.0,np.where(eth_ddh<=-0.20,0.50,0.25))
    print("="*90)
    print("BTC-LONG + ETH-SHORT (two-asset) vs BTC-only V2 — full 2017-2026")
    print("="*90)
    s=two_asset(btc,eth,bull,parab,eth_bear,eth_ss)
    cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])
    print(f"  {'BTC-long + ETH-short':<28}CAGR {cg*100:>4.0f}%  DD {dd*100:>4.0f}%  ret/DD {rr:.2f}  OOS {o[2]:.2f}")
    print(f"      year-by-year: "+" ".join(f"{y}:{yr(s,y):+.0f}%" for y in range(2018,2027) if yr(s,y) is not None))
    # reference: BTC-only V2
    from backtest_v2_combined import run2, build
    df2,bull2=build(); c2=df2["close"]
    from backtest_short_aggressive import daily_macd_bear
    L2=bull2&(c2>bt.sma(c2,9*30*BPD).shift(1)).fillna(False).values
    be2=((c2/c2.rolling(40*BPD).max().shift(1)-1)<-0.10).fillna(False).values & daily_macd_bear(df2)
    dh2=(c2/c2.rolling(180*BPD).max().shift(1)-1).fillna(0).values; ss2=np.where(dh2<=-0.30,1.0,np.where(dh2<=-0.20,0.50,0.25))
    pa2=(c2>2.2*bt.sma(c2,140*BPD).shift(1)).fillna(False).values
    sv=run2(df2,L2,be2,ss2,pa2,use_parab=True,use_bd=True); cg2,dd2,rr2=m(sv)
    print(f"  {'BTC-only V2 (ref)':<28}CAGR {cg2*100:>4.0f}%  DD {dd2*100:>4.0f}%  ret/DD {rr2:.2f}  OOS {m(sv[sv.index>=sv.index[int(len(sv)*0.6)]])[2]:.2f}")


if __name__=="__main__":
    main()
