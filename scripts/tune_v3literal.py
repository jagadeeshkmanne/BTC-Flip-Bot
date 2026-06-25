#!/usr/bin/env python3
"""tune_v3literal.py — stress-tune the LITERAL V3 reconstruction (scripts/backtest_v3_recon.py).

Question: can ANY config of the faithful V3 design (engulfing + RSI21 + MACD + ATR-vol-filter +
volume-spike, gated by daily-EMA50 + 4h-RSI14, long+short, SL-flip, partial-TP, pyramid, 2x base
leverage) beat the stripped-down my-V3 (CAGR 74%, DD -31%, ret/DD 2.38, OOS 3.0) on OOS ret/DD
WHILE NOT being fat-tail-fragile (survives remove-top-3-trades)?

Uses the REAL engine: imports run/load/macd from backtest_v3_recon. Leverage is a module global
(backtest_v3_recon.LEV), so we patch it per-candidate. Long-only = use_flip=False + rsi_smax=-1
(no ssig short signals AND no flips), exercising the genuine engine code path with shorts disabled.

OOS methodology mirrors the recon's main(): equity-slice OOS (last 40% of the equity curve),
because trades carry $PnL not timestamps. CAGR/DD/ret-DD on full + OOS slice.

ANTI-BUG: 2x cash/units engine -> distrust ret/DD>5 or OOS>6; we re-derive from the equity curve
returned by run() (single source of truth) and print raw equity endpoints so numbers are checkable.
"""
from __future__ import annotations
import os, sys, itertools
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_v3_recon as R
import bt_helpers as bt

CAP0 = R.CAP0
COMM = R.COMM; SLIP = R.SLIP


def run_logged(df, P, LEV, use_flip=True):
    """EXACT copy of backtest_v3_recon.run() but also logs per-trade return-on-equity-at-entry
    (ROE). This is the COMPOUNDING-INVARIANT basis needed for an honest remove-top-k test:
    the recon/tuner's dollar-PnL rm-top3 is INVALID for high-trade-count compounding configs
    (denominator stays CAP0 while biggest-$ trades are just the latest/largest-account ones).
    Returns (eqs, dollar_pnl_array, roe_array)."""
    o=df["open"].values;h=df["high"].values;l=df["low"].values;c=df["close"].values;v=df["volume"].values
    rsi=bt.rsi(df["close"],P["rsi_len"]).values
    ln=bt.ema(df["close"],12)-bt.ema(df["close"],26);ml=ln.values;ms=bt.ema(ln,9).values
    atr=bt.atr(df,20).values;atrma=pd.Series(atr).rolling(50).mean().values
    vsma=pd.Series(v).rolling(20).mean().values
    db=df["d_bull"].values;h4r=df["h4r"].values;body=np.abs(c-o)
    n=len(df);eq=CAP0;peak=CAP0;halt=-1
    pos=0;qty=0.0;entry=0.0;sl=0.0;partial=False;pyr=False;initsl=0.0;isflip=False;flipbar=-1
    lls=-9999;lss=-9999;lex=-9999;pend=0;pbar=-1;pref=0.0
    ec=np.full(n,CAP0);trades=[];roes=[];eae=[CAP0]
    def closeit(px):
        nonlocal eq,pos,qty,partial,pyr,isflip
        pnl=qty*(px-entry)-abs(qty)*px*(COMM+SLIP);eq+=pnl
        trades.append(pnl);roes.append(pnl/eae[0])
        pos=0;qty=0.0;partial=False;pyr=False;isflip=False
    for i in range(60,n-1):
        px=c[i];peak=max(peak,eq)
        if (eq-peak)/peak<=-P["dd_halt"] and pos==0: halt=i+P["halt_bars"];peak=eq
        halted=i<halt
        if pos!=0:
            slhit=(l[i]<=sl) if pos>0 else (h[i]>=sl)
            if slhit:
                wasflip=isflip;wlong=pos>0;closeit(sl);lex=i
                if wlong: lls=i
                else: lss=i
                if use_flip and not wasflip: pend=(-1 if wlong else 1);pbar=i;pref=sl
            elif isflip and (i-flipbar)>=P["flip_ts"]: closeit(px);lex=i
            else:
                Rm=((px-entry)/initsl) if pos>0 else ((entry-px)/initsl)
                if not partial and initsl>0 and Rm>=P["tp_r"]:
                    cq=qty*0.15;eq+=cq*(px-entry)-abs(cq)*px*(COMM+SLIP);qty-=cq;partial=True
                    sl=entry*(1+0.001) if pos>0 else entry*(1-0.001)
                if pos!=0 and not pyr and initsl>0 and Rm>=P["pyr_r"]:
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
        if pend!=0 and pos==0 and (i-pbar)>=1 and not halted and not xcd:
            if pend==1:
                swl=l[max(0,i-10):i+1].min();st=max(swl*(1-0.001),pref*(1-P["flip_cap"]))
                if px-st>0: qty=(eq*LEV)/px;entry=px;sl=st;pos=1;initsl=px-st;isflip=True;flipbar=i;eq-=eq*LEV*COMM;eae[0]=eq
            else:
                swh=h[max(0,i-10):i+1].max();st=min(swh*(1+0.001),pref*(1+P["flip_cap"]))
                if st-px>0: qty=-(eq*LEV)/px;entry=px;sl=st;pos=-1;initsl=st-px;isflip=True;flipbar=i;eq-=eq*LEV*COMM;eae[0]=eq
            pend=0
        if pos==0 and pend==0 and not halted and not xcd:
            if lsig and not lcd:
                st=max(min(l[i],l[i-1])*(1-0.001),px*(1-P["sl_max"]))
                if px-st>0: qty=(eq*LEV)/px;entry=px*(1+SLIP);sl=st;pos=1;partial=pyr=False;initsl=px-st;eq-=eq*LEV*COMM;eae[0]=eq
            elif ssig and not scd:
                st=min(max(h[i],h[i-1])*(1+0.001),px*(1+P["sl_max"]))
                if st-px>0: qty=-(eq*LEV)/px;entry=px*(1-SLIP);sl=st;pos=-1;partial=pyr=False;initsl=st-px;eq-=eq*LEV*COMM;eae[0]=eq
    ec[n-1]=eq if pos==0 else eq+qty*(c[n-1]-entry)
    eqs=pd.Series(ec,index=pd.to_datetime(df["timestamp"])).iloc[60:]
    return eqs,np.array(trades),np.array(roes)

# ---- base default P (the literal V3 defaults from recon main()) ----
BASE = dict(rsi_len=21, rsi_lmin=45, rsi_smax=55, eng=1.0, volr=1.5, sl_max=0.025,
            tp_r=6.0, pyr_r=3.0, cd=24, dd_halt=0.25, halt_bars=168, flip_ts=24, flip_cap=0.015)


def evaluate(df, P, lev, use_flip, long_only):
    """Run the literal-V3 engine and return an honest metrics dict, all re-derived from the
    equity curve + trade PnL array returned by run() (no separate accounting)."""
    P = dict(P)
    if long_only:
        P["rsi_smax"] = -1.0      # rsi[i] < -1 never true -> no short signals
        use_flip = False          # no SL-flip -> no flip-to-short either
    eqs, tr, roe = run_logged(df, P, lev, use_flip=use_flip)

    eqs = eqs[eqs > 0]
    if len(eqs) < 10 or len(tr) == 0:
        return None

    yrs = max((eqs.index[-1] - eqs.index[0]).days / 365.25, 1e-9)
    final = eqs.iloc[-1]
    total = final / CAP0 - 1.0
    cagr = (max(final, 1.0) / CAP0) ** (1 / yrs) - 1
    dd = (eqs / eqs.cummax() - 1).min()
    retdd = cagr / abs(dd) if dd < -1e-9 else 0.0

    # OOS = equity slice last 40% (same as recon main)
    cut = eqs.index[int(len(eqs) * 0.6)]
    eo = eqs[eqs.index >= cut]
    oc = odd = ordd = float("nan")
    if len(eo) > 5 and eo.iloc[0] > 0:
        oyrs = max((eo.index[-1] - eo.index[0]).days / 365.25, 1e-9)
        oc = (max(eo.iloc[-1], 1.0) / eo.iloc[0]) ** (1 / oyrs) - 1
        odd = (eo / eo.cummax() - 1).min()
        ordd = oc / abs(odd) if odd < -1e-9 else 0.0

    # fragility (FLAWED $-basis, kept only to expose the mirage): remove 3 best-$ trades,
    # denominator stays CAP0 -> meaningless for compounding configs.
    rt3 = None
    if len(tr) > 5:
        idx = np.argsort(tr)[::-1][:3]
        kept = tr.copy(); kept[idx] = 0.0
        rt3 = (np.cumsum(kept)[-1] + CAP0) / CAP0 - 1.0
    top3 = float(np.sort(tr)[::-1][:3].sum()) if len(tr) >= 3 else float(tr.sum())

    # HONEST fragility: per-trade return-on-equity (compounding-invariant). Remove top-k by ROE
    # and RECOMPOUND the remaining trades. rt3_roe<=0 -> profit lives in <=3 trades = fat-tail.
    def rm_top_roe(k):
        if len(roe) <= k:
            return None
        order = np.argsort(roe)[::-1]
        mask = np.ones(len(roe), bool); mask[order[:k]] = False
        return float(np.prod(1 + roe[mask]) - 1)
    rt3_roe = rm_top_roe(3); rt5_roe = rm_top_roe(5)
    # top-3 share of total log-growth
    lg = np.log1p(np.clip(roe, -0.999, None)); tl = lg.sum()
    top3_share = float(np.sort(lg)[::-1][:3].sum() / tl) if abs(tl) > 1e-9 else float("nan")
    max_roe = float(roe.max()) if len(roe) else 0.0

    w = tr[tr > 0]; ls = tr[tr <= 0]
    pf = w.sum() / abs(ls.sum()) if ls.sum() < 0 else 9.99
    winr = len(w) / len(tr) if len(tr) else 0.0

    return dict(cagr=cagr, dd=dd, retdd=retdd, total=total, final=final,
                oos_cagr=oc, oos_dd=odd, oos_retdd=ordd,
                ntr=len(tr), pf=pf, winr=winr, rt3=rt3, top3=top3, tot_pnl=float(tr.sum()),
                rt3_roe=rt3_roe, rt5_roe=rt5_roe, top3_share=top3_share, max_roe=max_roe)


def fmt(m, tag):
    if m is None:
        return f"  {tag:<46} -> no trades"
    r3r = f"{m['rt3_roe']*100:+5.0f}%" if m['rt3_roe'] is not None else " n/a "
    return (f"  {tag:<46} CAGR {m['cagr']*100:4.0f}% DD {m['dd']*100:4.0f}% "
            f"r/DD {m['retdd']:4.2f} | OOS r/DD {m['oos_retdd']:4.2f} | "
            f"n {m['ntr']:3d} PF {m['pf']:4.2f} | top3={m['top3_share']*100:3.0f}%growth "
            f"rmTop3(ROE) {r3r} maxROE {m['max_roe']*100:4.0f}%")


# ---- coordinate descent over the requested axes ----
AXES = dict(
    LEV=[1.0, 1.5, 2.0],
    sl_max=[0.025, 0.04, 0.06],
    tp_r=[4.0, 6.0, 8.0],
    pyr_r=[2.0, 3.0, 4.0],
    rsi_lmin=[40, 45, 50],
    rsi_smax=[50, 55, 60],
    eng=[0.8, 1.0, 1.5],
    volr=[1.0, 1.5, 2.0],
)


def candidate_key(lev, P, use_flip, long_only):
    return (lev, P["sl_max"], P["tp_r"], P["pyr_r"], P["rsi_lmin"], P["rsi_smax"],
            P["eng"], P["volr"], use_flip, long_only)


def main():
    df = R.load("4h")
    print("=" * 120)
    print(f"LITERAL-V3 STRESS TUNE on 4H  ({df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()})")
    print("my-V3 target: CAGR 74% DD -31% ret/DD 2.38 OOS-r/DD 3.0, robust (profit spread, green every yr)")
    print("=" * 120)

    results = {}   # key -> (metrics, lev, P, use_flip, long_only)

    def trial(lev, P, use_flip, long_only, store=True):
        k = candidate_key(lev, P, use_flip, long_only)
        if k in results:
            return results[k][0]
        m = evaluate(df, P, lev, use_flip, long_only)
        if store:
            results[k] = (m, lev, dict(P), use_flip, long_only)
        return m

    # baseline
    print("\n--- baselines (default P) ---")
    for uf, lo in [(True, False), (False, False), (False, True)]:
        m = trial(2.0, BASE, uf, lo)
        print(fmt(m, f"LEV2.0 flip={uf} long_only={lo}"))

    # Coordinate descent, run for each (use_flip, long_only) mode.
    # Objective: maximize OOS ret/DD SUBJECT to remove-top-3 staying positive (robustness).
    # We score = OOS r/DD but penalize fragile (rt3<=0) heavily so CD prefers robust configs.
    def score(m):
        if m is None or np.isnan(m.get("oos_retdd", np.nan)):
            return -1e9
        s = m["oos_retdd"]
        # HONEST robustness gate: ROE-based remove-top-3 (recompounded) must stay healthy.
        if m["rt3_roe"] is None or m["rt3_roe"] <= 0.5:   # rest must still ~>50% total over 7y
            s -= 100.0
        if m["ntr"] < 12:
            s -= 50.0    # too few trades = not credible
        return s

    modes = [("flip", True, False), ("noflip", False, False), ("longonly", False, True)]
    for mode_name, uf, lo in modes:
        print(f"\n--- coordinate descent: mode={mode_name} (use_flip={uf}, long_only={lo}) ---")
        cur = dict(BASE); cur_lev = 2.0
        best_m = trial(cur_lev, cur, uf, lo); best_s = score(best_m)
        for sweep in range(2):   # two passes
            improved = False
            # leverage axis
            for lev in AXES["LEV"]:
                m = trial(lev, cur, uf, lo)
                if score(m) > best_s:
                    best_s, best_m, cur_lev = score(m), m, lev; improved = True
            # param axes
            for ax in ("sl_max", "tp_r", "pyr_r", "rsi_lmin", "rsi_smax", "eng", "volr"):
                if lo and ax == "rsi_smax":
                    continue   # rsi_smax irrelevant in long-only
                for val in AXES[ax]:
                    trialP = dict(cur); trialP[ax] = val
                    m = trial(cur_lev, trialP, uf, lo)
                    if score(m) > best_s:
                        best_s, best_m, cur = score(m), m, trialP; improved = True
            if not improved:
                break
        print(fmt(best_m, f"[CD-best {mode_name}] LEV{cur_lev}"))
        print(f"      P={ {k:cur[k] for k in ('sl_max','tp_r','pyr_r','rsi_lmin','rsi_smax','eng','volr')} } lev={cur_lev}")

    # Also a focused full-grid over the most impactful axes (LEV, sl_max, tp_r, volr, mode)
    # to make sure CD didn't miss a robust pocket. Keep others at default.
    print("\n--- focused grid: LEV x sl_max x tp_r x volr x mode (others=default) ---")
    grid_axes = dict(LEV=AXES["LEV"], sl_max=AXES["sl_max"], tp_r=AXES["tp_r"], volr=AXES["volr"])
    for mode_name, uf, lo in modes:
        for lev, slm, tpr, vr in itertools.product(*grid_axes.values()):
            P = dict(BASE); P["sl_max"] = slm; P["tp_r"] = tpr; P["volr"] = vr
            trial(lev, P, uf, lo)

    # ---- collate: rank ALL stored candidates ----
    rows = []
    for k, (m, lev, P, uf, lo) in results.items():
        if m is None or np.isnan(m.get("oos_retdd", np.nan)):
            continue
        rows.append((m, lev, P, uf, lo))

    def robust(m):
        # HONEST: remove top-3 by ROE, recompound, require the rest still meaningfully positive
        return (m["rt3_roe"] is not None and m["rt3_roe"] > 0.5 and m["ntr"] >= 12
                and not np.isnan(m["oos_retdd"]))

    robust_rows = [r for r in rows if robust(r[0])]
    fragile_rows = [r for r in rows if not robust(r[0])]

    print("\n" + "=" * 120)
    print(f"TOTAL candidates evaluated: {len(rows)} | robust (rm-top3>0 & n>=12): {len(robust_rows)} | "
          f"fragile/thin: {len(fragile_rows)}")
    print("=" * 120)

    def show(title, rs, key, topn=8):
        print(f"\n### {title}")
        rs2 = sorted(rs, key=key, reverse=True)[:topn]
        for m, lev, P, uf, lo in rs2:
            mode = "LONGONLY" if lo else ("FLIP" if uf else "NOFLIP")
            tag = (f"{mode} L{lev} sl{P['sl_max']} tp{P['tp_r']:.0f} pyr{P['pyr_r']:.0f} "
                   f"rl{P['rsi_lmin']} rs{P['rsi_smax']} e{P['eng']} v{P['volr']}")
            print(fmt(m, tag))

    show("TOP by OOS ret/DD among HONESTLY-ROBUST configs (rmTop3-ROE>50%, n>=12)",
         robust_rows, key=lambda r: r[0]["oos_retdd"])
    show("TOP by OOS ret/DD overall (incl FRAGILE - note their rmTop3-ROE collapses)",
         rows, key=lambda r: r[0]["oos_retdd"])
    show("LEAST fat-tail-fragile overall (max rmTop3-ROE, recompounded)",
         rows, key=lambda r: (r[0]["rt3_roe"] if r[0]["rt3_roe"] is not None else -9))

    # ---- final: best 3 robust (or, if none robust, best 3 by OOS r/DD flagged fragile) ----
    if robust_rows:
        finalists = sorted(robust_rows, key=lambda r: r[0]["oos_retdd"], reverse=True)[:3]
        verdict_pool = "ROBUST"
    else:
        finalists = sorted(rows, key=lambda r: r[0]["oos_retdd"], reverse=True)[:3]
        verdict_pool = "FRAGILE (no robust config found)"

    print("\n" + "=" * 120)
    print(f"FINAL: best 3 literal-V3 configs (pool={verdict_pool})")
    print("=" * 120)
    for i, (m, lev, P, uf, lo) in enumerate(finalists, 1):
        mode = "LONGONLY" if lo else ("FLIP" if uf else "NOFLIP")
        print(f"\n[{i}] mode={mode} LEV={lev} P={ {k:P[k] for k in ('sl_max','tp_r','pyr_r','rsi_lmin','rsi_smax','eng','volr')} }")
        print(f"    CAGR {m['cagr']*100:.0f}%  maxDD {m['dd']*100:.0f}%  ret/DD {m['retdd']:.2f}  "
              f"final ${m['final']:,.0f}  total {m['total']*100:+.0f}%")
        print(f"    OOS-slice: CAGR {m['oos_cagr']*100:.0f}%  DD {m['oos_dd']*100:.0f}%  ret/DD {m['oos_retdd']:.2f}")
        print(f"    trades {m['ntr']}  PF {m['pf']:.2f}  win% {m['winr']*100:.0f}  tot-PnL ${m['tot_pnl']:,.0f}")
        rt3 = f"{m['rt3']*100:+.0f}%" if m['rt3'] is not None else "n/a"
        print(f"    [$-basis rm-top3, FLAWED/inflated]: {rt3}  (top3=${m['top3']:,.0f})")
        r3r = f"{m['rt3_roe']*100:+.0f}%" if m['rt3_roe'] is not None else "n/a"
        r5r = f"{m['rt5_roe']*100:+.0f}%" if m['rt5_roe'] is not None else "n/a"
        verd = 'ROBUST' if (m['rt3_roe'] is not None and m['rt3_roe'] > 0.5) else 'FAT-TAIL FRAGILE'
        print(f"    [ROE-basis, HONEST] rm-top3 recompounded: {r3r}  rm-top5: {r5r}  | "
              f"top-3 = {m['top3_share']*100:.0f}% of log-growth | maxROE 1 trade {m['max_roe']*100:.0f}%  -> {verd}")

    # year-by-year for the best finalist
    if finalists:
        m, lev, P, uf, lo = finalists[0]
        Pf = dict(P)
        if lo:
            Pf["rsi_smax"] = -1.0; uf2 = False
        else:
            uf2 = uf
        eqs, tr, _ = run_logged(df, Pf, lev, use_flip=uf2)
        eqs = eqs[eqs > 0]
        print("\n--- YEAR-BY-YEAR for finalist [1] ---")
        yr = eqs.groupby(eqs.index.year)
        prev = None
        for y, grp in yr:
            start = grp.iloc[0] if prev is None else prev
            ret = grp.iloc[-1] / start - 1
            dd = (grp / grp.cummax() - 1).min()
            print(f"    {y}: ret {ret*100:+6.0f}%  intra-yr DD {dd*100:5.0f}%  endEq ${grp.iloc[-1]:,.0f}")
            prev = grp.iloc[-1]

    print("\n(NOTE: OOS here = last-40% equity slice, same method as recon main(). "
          "2x cash/units engine -> all metrics re-derived from the run() equity curve.)")


if __name__ == "__main__":
    main()
