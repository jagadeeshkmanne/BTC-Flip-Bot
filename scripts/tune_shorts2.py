#!/usr/bin/env python3
"""tune_shorts2.py — deeply tune the my-V3 SHORT engine for max ADDITIVE bear-year profit.

LONG side is PINNED to the my-V3 winner (MTF bull gate, atr_mult=3.5, be_buf=0.01, BE@1R,
pyramid@2R). We vary ONLY the short engine and measure the combined long+short result:
  1. GATE  : when are shorts allowed (price<SMA(N mo); drawdown>D% off ATH; daily EMA50<EMA200
             rollover; price<SMA(12mo) AND below 50d MA / falling).
  2. SIZE  : fixed {0.15,0.25,0.4,0.5} and a VOL-SCALED size (target-vol / ATR%, capped).
  3. STOP  : s_atr {3,4,5} x s_cap {0.08,0.12,0.15}; plus a chandelier TRAIL on the short to
             ride a bear leg down (ratchets the stop DOWN as price falls).
  4. PYRAMID: short pyramid layers (0,1,2) at +R triggers — bears fall fast, does adding help?
  5. RE-ENTRY: one short per bear regime (armed-once) vs multiple re-entries within a regime.

Honesty / anti-bug:
  * allow_short=False MUST reproduce the my-V3 long-only base byte-for-byte (asserted at start).
  * Short accounting (verified): units<0; equity = cash+units*price so profit when price FALLS;
    stop is ABOVE entry and triggers on high>=stop; BE/trail/pyramid ratchet the stop DOWN.
  * No lookahead: every SMA / ATH / rollover gate is .shift(1) (decided on the PRIOR closed bar).
  * Only 2 bears exist (2022, 2026). We report #short-trades and demand a gate work in BOTH
    bears; configs that shine via one lucky episode are flagged, not trusted.

Engine is the unified signed cash/units engine from backtest_myv3_final.run, extended with:
short trailing stop, configurable short pyramid layers, vol-scaled size, and re-entry.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_shorts import regimes, m
from backtest_myv3_final import run as run_final

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT
BPD = 6  # 4h bars per day


# ---------------------------------------------------------------------------
# Extended unified engine. LONG path is IDENTICAL to backtest_myv3_final.run.
# Short path adds: trailing short stop, N pyramid layers, vol-scaled size, re-entry.
# ---------------------------------------------------------------------------
def run(df, bull, bear, allow_long=True, allow_short=False, lev=1.0,
        # --- pinned long params ---
        pyr_r=2.0, pyr_frac=1.0, be_r=1.0, atr_mult=3.5, sl_cap=0.08, be_buf=0.01,
        # --- short params ---
        short_size=0.25, s_atr=4.0, s_cap=0.12, s_pyr_r=2.0, s_pyr_layers=1, s_pyr_frac=1.0,
        s_trail_k=None, s_reentry=False, s_vol_scale=False, s_vol_target=0.04, s_vol_cap=0.6):
    o=df["open"].values; h=df["high"].values; l=df["low"].values; c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    atrpct = a / np.where(c>0, c, 1e-9)   # ATR as % of price (for vol scaling), causal at bar i
    cash=1.0; units=0.0; side=0; entry=0.0; stop=0.0; R=0.0; notional0=0.0
    pyr_done=0; peakH=0.0; troughL=0.0
    armedL=True; armedS=True
    eq=np.ones(n)
    for i in range(16, n-1):
        oN,hN,lN,cN = o[i+1],h[i+1],l[i+1],c[i+1]
        # re-arm when leaving a regime
        if not bull[i]: armedL=True
        if not bear[i]:
            armedS=True
        elif s_reentry:
            armedS=True   # bear still on -> allow re-entry after a flat
        if side!=0:
            # ---- short chandelier trail: ratchet stop DOWN as price makes new lows ----
            if side==-1 and s_trail_k is not None:
                troughL=min(troughL,l[i]); stop=min(stop, troughL + s_trail_k*a[i])
            hit = (lN<=stop) if side==1 else (hN>=stop)
            regime_out = (not bull[i]) if side==1 else (not bear[i])
            if hit:
                fpx = stop*(1-SLIP) if side==1 else stop*(1+SLIP)
                cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyr_done=0
            elif regime_out:
                fpx = oN*(1-SLIP) if side==1 else oN*(1+SLIP)
                cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyr_done=0
            else:
                prof = ((cN-entry)/R) if side==1 else ((entry-cN)/R)
                if prof>=be_r:
                    be = entry*(1+be_buf) if side==1 else entry*(1-be_buf)
                    stop = max(stop,be) if side==1 else min(stop,be)
                # pyramid (long: single layer @ pyr_r; short: up to s_pyr_layers @ multiples of s_pyr_r)
                if side==1:
                    if pyr_r is not None and pyr_done==0 and prof>=pyr_r:
                        addn=pyr_frac*notional0; add_units=addn/cN
                        cash -= add_units*cN + addn*FEE; units += add_units; pyr_done=1
                        stop=max(stop,entry)
                else:
                    if s_pyr_r is not None and pyr_done<s_pyr_layers and prof>=s_pyr_r*(pyr_done+1):
                        addn=s_pyr_frac*notional0; add_units=-addn/cN
                        cash -= add_units*cN + addn*FEE; units += add_units; pyr_done+=1
                        stop=min(stop,entry)   # lock to entry once in profit
        if side==0:
            go=0
            if allow_long and bull[i] and armedL: go=1
            elif allow_short and bear[i] and armedS: go=-1
            if go!=0:
                E=cash
                if go==1:
                    st=max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap)); entry=oN*(1+SLIP); sz=lev
                else:
                    st=min(c[i]+s_atr*a[i], c[i]*(1+s_cap)); entry=oN*(1-SLIP)
                    sz=lev*short_size
                    if s_vol_scale:
                        # smaller when ATR% high: size *= clamp(target/atrpct, .., vol_cap/short_size scale)
                        scale = s_vol_target / max(atrpct[i], 1e-6)
                        sz = lev*short_size*min(max(scale,0.0), s_vol_cap/short_size)
                ok = (entry-st>0) if go==1 else (st-entry>0)
                if ok:
                    notional0 = sz*E
                    units = go*notional0/entry; cash = E - units*entry - notional0*FEE
                    stop=st; R=abs(entry-st); side=go; pyr_done=0
                    peakH=entry; troughL=entry
                    if go==1: armedL=False
                    else: armedS=False
        eq[i+1] = cash + units*cN
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]


# ---------------------------------------------------------------------------
# instrumented variant that also counts SHORT entries (for thin-sample honesty)
# ---------------------------------------------------------------------------
def n_shorts(df, bull, bear, **kw):
    """Replays the same logic just to count short entries (cheap, separate to keep run() clean)."""
    o=df["open"].values; c=df["close"].values; h=df["high"].values; l=df["low"].values
    a=bt.atr(df,14).values; n=len(df)
    side=0; armedS=True; armedL=True; count=0
    allow_long=kw.get("allow_long",True); allow_short=kw.get("allow_short",False)
    s_reentry=kw.get("s_reentry",False)
    atr_mult=kw.get("atr_mult",3.5); sl_cap=kw.get("sl_cap",0.08)
    s_atr=kw.get("s_atr",4.0); s_cap=kw.get("s_cap",0.12)
    s_trail_k=kw.get("s_trail_k",None)
    entry=stop=0.0; troughL=0.0; cash=1.0  # cash only nominal; sizing irrelevant for count
    for i in range(16,n-1):
        oN,hN,lN=o[i+1],h[i+1],l[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        elif s_reentry: armedS=True
        if side!=0:
            if side==-1 and s_trail_k is not None:
                troughL=min(troughL,l[i]); stop=min(stop, troughL + s_trail_k*a[i])
            hit=(lN<=stop) if side==1 else (hN>=stop)
            regime_out=(not bull[i]) if side==1 else (not bear[i])
            if hit or regime_out: side=0
        if side==0:
            go=0
            if allow_long and bull[i] and armedL: go=1
            elif allow_short and bear[i] and armedS: go=-1
            if go!=0:
                if go==1:
                    st=max(c[i]-atr_mult*a[i],c[i]*(1-sl_cap)); entry=oN*(1+SLIP); ok=entry-st>0
                else:
                    st=min(c[i]+s_atr*a[i],c[i]*(1+s_cap)); entry=oN*(1-SLIP); ok=st-entry>0
                if ok:
                    stop=st; side=go; troughL=entry
                    if go==1: armedL=False
                    else:
                        armedS=False; count+=1
    return count


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else 0.0


# ---------------------------------------------------------------------------
def build_gates(df):
    """Return list of (label, bear_array). All gates CAUSAL via .shift(1)."""
    c=df["close"]
    gates=[]
    # --- daily-EMA rollover gate (the my-V3 default bear): EMA50<EMA200, prior-day confirmed ---
    df_, bull_, bear_daily = GLOBAL_REG
    gates.append(("daily EMA50<200 (current default)", bear_daily))
    # 1) price < SMA(N months)
    for mo in (9,12,15,18):
        sma=bt.sma(c,mo*30*BPD).shift(1)
        gates.append((f"price<SMA({mo}mo)", (c<sma).fillna(False).values))
    # 2) drawdown > D% off all-time-high (expanding max, shifted = no lookahead)
    ath=c.cummax().shift(1)
    ddv=(c/ath-1)
    for d in (0.15,0.20,0.25):
        gates.append((f"DD>{int(d*100)}% off ATH", (ddv<-d).fillna(False).values))
    # 3) price<SMA(12mo) AND below 50d MA (falling-confirmation)
    sma12=bt.sma(c,12*30*BPD).shift(1); ma50=bt.sma(c,50*BPD).shift(1)
    gates.append(("price<SMA(12mo) & <50dMA", ((c<sma12)&(c<ma50)).fillna(False).values))
    return gates


GLOBAL_REG=None


def hdr():
    print(f"  {'config':<46}{'CAGR':>6}{'DD':>6}{'r/DD':>6}{'OOS':>7}{'2022':>6}{'2026':>7}{'#sh':>5}")


def line(df,bull,bear,label,**kw):
    s=run(df,bull,bear,**kw); cg,dd,rr=m(s)
    o=m(s[s.index>=s.index[int(len(s)*0.6)]])
    ns=n_shorts(df,bull,bear,**kw) if kw.get("allow_short") else 0
    print(f"  {label:<46}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o[2]:>7.2f}{yr(s,2022):>6.0f}%{yr(s,2026):>6.0f}%{ns:>5}")
    return dict(label=label,cagr=cg,dd=dd,rdd=rr,oos=o[2],y22=yr(s,2022),y26=yr(s,2026),nsh=ns)


def main():
    global GLOBAL_REG
    df,bull,bear_daily=regimes()
    GLOBAL_REG=(df,bull,bear_daily)
    c=df["close"]
    bear_slow=(c < bt.sma(c,12*30*6).shift(1)).fillna(False).values   # current short gate

    print("="*100)
    print("TUNE-SHORTS-2 — deep short tuning, LONG side PINNED (atr3.5,bebuf1%). Combined BTC 4h.")
    print("="*100)

    # ===== ANTI-BUG: allow_short=False must reproduce long-only base byte-for-byte =====
    from backtest_myv3_shorts import run as run_base
    base_long = run_base(df,bull,bear_daily,allow_long=True,allow_short=False,atr_mult=3.5,be_buf=0.01)
    mine_long = run(df,bull,bear_daily,allow_long=True,allow_short=False)
    maxdiff = float(np.max(np.abs(base_long.values - mine_long.values)))
    cgL,ddL,rrL=m(mine_long)
    assert maxdiff < 1e-12, f"ANTI-BUG FAIL: short-off does not match long-only base (maxdiff={maxdiff})"
    print(f"  [anti-bug] allow_short=False == long-only base byte-for-byte  (maxdiff={maxdiff:.2e})  OK")
    print(f"  [base] LONG-ONLY: CAGR {cgL*100:.0f}%  DD {ddL*100:.0f}%  r/DD {rrL:.2f}")
    print()

    # ===== reference: the CURRENT combined short =====
    hdr()
    cur=line(df,bull,bear_slow,"CURRENT short (SMA12mo,sz.25,atr4,cap12,pyr1)",allow_short=True)
    print("  "+"-"*96)

    results=[]

    # ===================== 1) GATE SWEEP (default short size/stop/pyramid) =====================
    print("\n  [1] GATE SWEEP — short_size=.25, s_atr=4, s_cap=.12, pyr1 layer, one-per-regime")
    hdr()
    gates=build_gates(df)
    gate_map={}
    for label,bear in gates:
        r=line(df,bull,bear,f"gate: {label}",allow_short=True); r["gate"]=label; r["bear"]=bear
        results.append(r); gate_map[label]=bear

    # pick the gate that is positive in BOTH bears AND best combined r/DD for downstream tuning
    both_bears=[r for r in results if r["y22"]>0 and r["y26"]>0]
    both_bears.sort(key=lambda r:-r["rdd"])
    print("\n  Gates POSITIVE in BOTH 2022 AND 2026 (ranked by combined r/DD):")
    for r in both_bears:
        print(f"    {r['gate']:<40} r/DD {r['rdd']:.2f}  2022 {r['y22']:+.0f}%  2026 {r['y26']:+.0f}%  #sh {r['nsh']}")
    best_gate_label = both_bears[0]["gate"] if both_bears else "price<SMA(12mo)"
    G = gate_map[best_gate_label]
    print(f"\n  -> carrying best both-bear gate into tuning: '{best_gate_label}'")

    # ===================== 2) SIZE SWEEP (on best gate) =====================
    print(f"\n  [2] SIZE SWEEP on gate '{best_gate_label}' (s_atr=4,cap=.12,pyr1)")
    hdr()
    for sz in (0.15,0.25,0.40,0.50):
        results.append(line(df,bull,G,f"size={sz}",allow_short=True,short_size=sz))
    # vol-scaled size variants
    for tgt in (0.03,0.04,0.05):
        results.append(line(df,bull,G,f"size=VOL-SCALED(tgt{tgt})",allow_short=True,
                            short_size=0.4,s_vol_scale=True,s_vol_target=tgt,s_vol_cap=0.6))

    # ===================== 3) STOP SWEEP (on best gate, size .4) =====================
    print(f"\n  [3] STOP SWEEP on gate '{best_gate_label}' (size=.4,pyr1)")
    hdr()
    for sa in (3,4,5):
        for sc in (0.08,0.12,0.15):
            results.append(line(df,bull,G,f"s_atr={sa},cap={sc}",allow_short=True,
                                short_size=0.4,s_atr=float(sa),s_cap=sc))
    print("    -- short chandelier TRAIL (ride the bear leg down) --")
    for tk in (3,4,5):
        results.append(line(df,bull,G,f"TRAIL k={tk}",allow_short=True,short_size=0.4,s_trail_k=float(tk)))

    # ===================== 4) PYRAMID SWEEP =====================
    print(f"\n  [4] SHORT PYRAMID on gate '{best_gate_label}' (size=.4,atr4,cap12)")
    hdr()
    for L in (0,1,2):
        for pr in (1.5,2.0):
            results.append(line(df,bull,G,f"pyr layers={L} @ {pr}R",allow_short=True,
                                short_size=0.4,s_pyr_layers=L,s_pyr_r=pr))

    # ===================== 5) RE-ENTRY =====================
    print(f"\n  [5] RE-ENTRY on gate '{best_gate_label}' (size=.4,atr4,cap12,pyr1)")
    hdr()
    results.append(line(df,bull,G,"one-per-regime (default)",allow_short=True,short_size=0.4,s_reentry=False))
    results.append(line(df,bull,G,"multi re-entry per regime",allow_short=True,short_size=0.4,s_reentry=True))

    # ===================== 6) HAND-PICKED COMBINED CANDIDATES across gates =====================
    print("\n  [6] HAND-PICKED COMBO CANDIDATES (best ideas stacked, multiple gates)")
    hdr()
    combo_gates = {lab:bear for lab,bear in gates}
    cand=[]
    # for each gate that fired in both bears, test a richer short
    for lab in [r["gate"] for r in both_bears][:4]:
        bb=gate_map[lab]
        cand.append((f"[{lab}] sz.4,atr5,cap15",dict(short_size=0.4,s_atr=5.0,s_cap=0.15)))
        cand.append((f"[{lab}] sz.4,trail4",dict(short_size=0.4,s_trail_k=4.0)))
        cand.append((f"[{lab}] sz.4,pyr2@1.5,reentry",dict(short_size=0.4,s_pyr_layers=2,s_pyr_r=1.5,s_reentry=True)))
        for label2,kw2 in cand[-3:]:
            results.append(line(df,bull,bb,label2,allow_short=True,**kw2))

    # ===================== 7) FINE-GRAINED FRONTIER (size 0.25-0.4 x stop, on 12mo & 18mo gates) =====================
    print("\n  [7] FRONTIER PROBE — can we ADD bear profit while holding r/DD>=3.0 & DD>=-34%?")
    hdr()
    g12=gate_map.get("price<SMA(12mo)"); g18=gate_map.get("price<SMA(18mo)")
    for gl,gg in (("SMA12mo",g12),("SMA18mo",g18)):
        for sz in (0.30,0.35,0.40):
            for sa,sc in ((4,0.12),(5,0.15)):
                results.append(line(df,bull,gg,f"{gl} sz{sz} atr{sa} cap{sc}",allow_short=True,
                                    short_size=sz,s_atr=float(sa),s_cap=sc))

    # ===================== FINAL RANKING =====================
    cur_rdd=cur["rdd"]; cur_dd=cur["dd"]
    # dedupe, keep only real short configs positive in BOTH bears (anti-lucky-episode)
    seen=set(); uniq=[]
    for r in results:
        if r.get("nsh",0)<=0 or r["label"] in seen: continue
        seen.add(r["label"]); uniq.append(r)

    print("\n"+"="*100)
    print("FINAL-A — robust r/DD: DD<=34% AND positive in BOTH 2022 & 2026, ranked by r/DD")
    print("="*100)
    print(f"  CURRENT: r/DD {cur['rdd']:.2f}  DD {cur['dd']*100:.0f}%  CAGR {cur['cagr']*100:.0f}%  "
          f"2022 {cur['y22']:+.0f}%  2026 {cur['y26']:+.0f}%  #sh {cur['nsh']}")
    print("  "+"-"*96); hdr()
    A=[r for r in uniq if r["dd"]>=-0.34 and r["y22"]>0 and r["y26"]>0]
    A.sort(key=lambda r:-r["rdd"])
    for r in A[:12]:
        beat="  <==BEAT r/DD" if r["rdd"]>cur_rdd else ""
        print(f"  {r['label']:<46}{r['cagr']*100:>5.0f}%{r['dd']*100:>5.0f}%{r['rdd']:>6.2f}"
              f"{r['oos']:>7.2f}{r['y22']:>6.0f}%{r['y26']:>6.0f}%{r['nsh']:>5}{beat}")

    print("\n"+"="*100)
    print("FINAL-B — most ADDITIVE bear profit: DD<=34% AND positive BOTH bears, ranked by 2022+2026")
    print("="*100)
    print(f"  CURRENT adds: 2022 {cur['y22']:+.0f}%  2026 {cur['y26']:+.0f}%  (sum {cur['y22']+cur['y26']:+.0f})"
          f"  at r/DD {cur['rdd']:.2f}, DD {cur['dd']*100:.0f}%")
    print("  "+"-"*96); hdr()
    B=[r for r in uniq if r["dd"]>=-0.34 and r["y22"]>0 and r["y26"]>0]
    B.sort(key=lambda r:-(r["y22"]+r["y26"]))
    for r in B[:12]:
        add="  <==more bear$" if (r["y22"]+r["y26"])>(cur["y22"]+cur["y26"]) and r["rdd"]>=3.15 else ""
        print(f"  {r['label']:<46}{r['cagr']*100:>5.0f}%{r['dd']*100:>5.0f}%{r['rdd']:>6.2f}"
              f"{r['oos']:>7.2f}{r['y22']:>6.0f}%{r['y26']:>6.0f}%{r['nsh']:>5}{add}")
    if not A and not B:
        print("  (none passed filters)")


if __name__=="__main__":
    main()
