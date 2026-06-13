import numpy as np
import pandas as pd
import os

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
INITIAL_BAL = 5000.0
PER_LEG_NOTIONAL = INITIAL_BAL * 0.95 * 5.0 / 2.0   # $11,875 linear
RSI_LONG, RSI_SHORT = 35, 65
GAP_MIN = 0.0020
ATR_MAX_PCT = 0.80
DCA_SPACING = 0.005
TP_SINGLE = 0.005
SL_L1 = 0.006            # Deployed L1 SL (0.6%)
BE_WAIT_BARS = 6
TRAIL_ARM = 0.05         # L2 trail arm threshold (0.05%)
TRAIL_BUF = 0.025        # L2 trail buffer (0.025%)
COOLDOWN_BARS = 3        # 15 min cooldown from exit
DAILY_CAP_PCT = 0.04
FEES_TAKER = 0.00055
SLIPPAGE = 0.0002

def wilder_rsi(close, n):
    d = close.diff()
    gain = d.clip(lower=0)
    loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    al = loss.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + ag / al.replace(0, np.nan))

def prep():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    df["rsi"] = wilder_rsi(df["close"], 9)
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    df["atr_pct"] = tr.rolling(14).mean() / df["close"] * 100
    dfx = df.set_index("timestamp")
    c15 = dfx["close"].resample("15min", label="left", closed="left").last().dropna()
    e20 = c15.ewm(span=20, adjust=False, min_periods=20).mean()
    e50 = c15.ewm(span=50, adjust=False, min_periods=50).mean()
    t15 = pd.DataFrame({"closed_at": c15.index + pd.Timedelta(minutes=15),
                        "trend": np.where(e20 > e50, 1.0, -1.0),
                        "gap": (e20 - e50).abs() / e50})
    t15.loc[e20.isna() | e50.isna(), ["trend", "gap"]] = np.nan
    m = pd.merge_asof(df, t15.sort_values("closed_at"),
                      left_on="timestamp", right_on="closed_at",
                      direction="backward", allow_exact_matches=True)
    return m

def run_backtest(bt, tp_dca=0.0100, time_sl=144, l2_stop_pct=None, mtm_stop_pct=0.03):
    """
    Backtest engine with optional L2 hard stop-loss (l2_stop_pct) from average entry.
    """
    ts = bt["timestamp"].values
    O, H, L, C = bt["open"].values, bt["high"].values, bt["low"].values, bt["close"].values
    RSI, ATRP = bt["rsi"].values, bt["atr_pct"].values
    TR15, GAP15 = bt["trend"].values, bt["gap"].values
    n = len(bt)
    
    balance = INITIAL_BAL
    pos = None
    pending = None
    cooldown_until_i = -1
    daily_loss = {}
    trades = []
    
    exits = {"TP": 0, "BE-DCA": 0, "L2_TRAIL": 0, "SL": 0, "TREND_FLIP": 0, "TIME_SL": 0, "MTM_STOP": 0}
    
    for i in range(n):
        o, h, l, c = O[i], H[i], L[i], C[i]
        trend, gap = TR15[i], GAP15[i]
        
        # 1. Check entry signal at bar open (queued from prior bar close)
        if pos is None and pending is not None:
            side, atrp_sig = pending
            pending = None
            t_open = pd.Timestamp(ts[i])
            
            # Fix the daily loss cap bug when balance goes negative:
            cap_base = max(0.1, balance)
            day_cap = DAILY_CAP_PCT * cap_base
            
            ok = (not np.isnan(trend) and not np.isnan(gap) and gap >= GAP_MIN
                  and i >= cooldown_until_i
                  and daily_loss.get(t_open.date(), 0.0) > -day_cap)
            
            if ok:
                eff = o * (1 + SLIPPAGE * side)
                qty = PER_LEG_NOTIONAL / eff
                pos = {
                    "side": side, "avg": eff, "worst": eff, "qty": qty,
                    "legs": 1, "open_i": i, "l2_i": -1,
                    "entry_trend": trend, "peak_fav": 0.0,
                    "fees_in": eff * qty * FEES_TAKER
                }
                
        if pos is not None:
            side = pos["side"]
            l2_this_bar = False
            
            # 2. DCA Check
            if pos["legs"] == 1:
                trig = pos["worst"] * (1 - DCA_SPACING * side)
                if (l <= trig) if side == 1 else (h >= trig):
                    eff = trig * (1 + SLIPPAGE * side)
                    q2 = PER_LEG_NOTIONAL / eff
                    pos["fees_in"] += eff * q2 * FEES_TAKER
                    pos["avg"] = (pos["avg"] * pos["qty"] + eff * q2) / (pos["qty"] + q2)
                    pos["qty"] += q2
                    pos["worst"] = min(pos["worst"], eff) if side == 1 else max(pos["worst"], eff)
                    pos["legs"] = 2
                    pos["l2_i"] = i
                    l2_this_bar = True
                    
            avg = pos["avg"]
            
            # 3. Check MTM Stop
            if mtm_stop_pct is not None:
                lvl = avg - side * mtm_stop_pct * balance / pos["qty"]
                if (l <= lvl) if side == 1 else (h >= lvl):
                    exit_px = min(lvl, o) if side == 1 else max(lvl, o)
                    # Exit trade
                    eff_exit = exit_px * (1 - SLIPPAGE * side)
                    gross = (eff_exit - avg) * pos["qty"] * side
                    net = gross - eff_exit * pos["qty"] * FEES_TAKER - pos["fees_in"]
                    balance += net
                    trades.append((net, "MTM_STOP", pd.Timestamp(ts[i])))
                    exits["MTM_STOP"] += 1
                    pos = None
                    cooldown_until_i = i + 1 + COOLDOWN_BARS
                    continue
                    
            # 4. Standard Exits (TP / SL / BE-DCA)
            if pos is not None and not l2_this_bar:
                tp_px = avg * (1 + (TP_SINGLE if pos["legs"] == 1 else tp_dca) * side)
                
                # SL price math
                sl_px = None
                sl_reason = None
                
                if pos["legs"] == 1:
                    sl_px = pos["worst"] * (1 - SL_L1 * side)
                    sl_reason = "SL"
                else:
                    # L2 has filled
                    be_armed = (i - pos["l2_i"]) >= BE_WAIT_BARS
                    if be_armed:
                        pk = pos["peak_fav"]
                        if pk >= TRAIL_ARM:
                            sl_px = avg * (1 + (pk - TRAIL_BUF) / 100.0 * side)
                            sl_reason = "L2_TRAIL"
                        else:
                            sl_px = avg
                            sl_reason = "BE-DCA"
                    elif l2_stop_pct is not None:
                        # Hard stop loss for L2 active during wait period
                        sl_px = avg * (1 - l2_stop_pct * side)
                        sl_reason = "SL"
                
                tp_hit = (h >= tp_px) if side == 1 else (l <= tp_px)
                sl_hit = sl_px is not None and ((l <= sl_px) if side == 1 else (h >= sl_px))
                
                if sl_hit:
                    # Stop exit filled at worse of stop price or open
                    exit_px = min(sl_px, o) if side == 1 else max(sl_px, o)
                    eff_exit = exit_px * (1 - SLIPPAGE * side)
                    gross = (eff_exit - avg) * pos["qty"] * side
                    net = gross - eff_exit * pos["qty"] * FEES_TAKER - pos["fees_in"]
                    balance += net
                    if net < 0:
                        dkey = pd.Timestamp(ts[i]).date()
                        daily_loss[dkey] = daily_loss.get(dkey, 0.0) + net
                    trades.append((net, sl_reason, pd.Timestamp(ts[i])))
                    exits[sl_reason] += 1
                    pos = None
                    cooldown_until_i = i + 1 + COOLDOWN_BARS
                elif tp_hit:
                    # TP exit
                    exit_px = max(tp_px, o) if side == 1 else min(tp_px, o)
                    eff_exit = exit_px * (1 - SLIPPAGE * side)
                    gross = (eff_exit - avg) * pos["qty"] * side
                    net = gross - eff_exit * pos["qty"] * FEES_TAKER - pos["fees_in"]
                    balance += net
                    trades.append((net, "TP", pd.Timestamp(ts[i])))
                    exits["TP"] += 1
                    pos = None
            
            # 5. Trend Flip Exit (Profit-only) at close
            if pos is not None and not np.isnan(trend) and trend != pos["entry_trend"]:
                if (c - avg) * pos["qty"] * side > 0:
                    exit_px = c
                    eff_exit = exit_px * (1 - SLIPPAGE * side)
                    gross = (eff_exit - avg) * pos["qty"] * side
                    net = gross - eff_exit * pos["qty"] * FEES_TAKER - pos["fees_in"]
                    balance += net
                    trades.append((net, "TREND_FLIP", pd.Timestamp(ts[i])))
                    exits["TREND_FLIP"] += 1
                    pos = None
                    
            # 6. Time SL Exit
            if pos is not None and (i - pos["open_i"]) >= time_sl:
                # Losers only
                if (c - avg) * pos["qty"] * side < 0:
                    exit_px = c
                    eff_exit = exit_px * (1 - SLIPPAGE * side)
                    gross = (eff_exit - avg) * pos["qty"] * side
                    net = gross - eff_exit * pos["qty"] * FEES_TAKER - pos["fees_in"]
                    balance += net
                    trades.append((net, "TIME_SL", pd.Timestamp(ts[i])))
                    exits["TIME_SL"] += 1
                    pos = None
                    cooldown_until_i = i + 1 + COOLDOWN_BARS
                    
            # 7. Update Excursion Peak
            if pos is not None and pos["legs"] == 2:
                fav = (c - avg) / avg * 100.0 * side
                pos["peak_fav"] = max(pos["peak_fav"], fav)
                
        # Queuing entry signals
        if pos is None and pending is None:
            a = ATRP[i]
            if np.isnan(a) or a > ATR_MAX_PCT:
                continue
            r = RSI[i]
            if np.isnan(r):
                continue
            if r <= RSI_LONG:
                pending = (1, a)
            elif r >= RSI_SHORT:
                pending = (-1, a)
                
    return balance, trades, exits

def main():
    print("Prepping data...")
    bt = prep()
    print(f"Data ready: {len(bt):,} bars.")
    
    # We sweep:
    # 1. l2_stop_pct: None (baseline), 0.006 (0.6%), 0.010 (1.0%), 0.015 (1.5%), 0.020 (2.0%)
    # 2. mtm_stop_pct: None, 0.02, 0.03, 0.04
    # 3. tp_dca: 0.0025 (0.25%), 0.0050 (0.50%), 0.0100 (1.00%)
    
    results = []
    
    print("\nSweeping combinations (honest fills + real taker fees)...")
    print(f"{'L2 Stop':<10} {'MTM Stop':<10} {'TP DCA':<8} {'Trades':>7} {'Win%':>6} {'PF':>6} {'P&L ($)':>12}  Exits")
    print("-" * 100)
    
    for l2_stop in [None, 0.006, 0.010, 0.015, 0.020]:
        for mtm_stop in [None, 0.02, 0.03, 0.04]:
            for tp_dca in [0.0025, 0.0050, 0.0100]:
                bal, tr, ex = run_backtest(bt, tp_dca=tp_dca, l2_stop_pct=l2_stop, mtm_stop_pct=mtm_stop)
                
                if len(tr) == 0:
                    continue
                
                pnls = np.array([t[0] for t in tr])
                w = sum(1 for p in pnls if p > 0)
                lo = sum(1 for p in pnls if p < 0)
                win_pct = w / len(pnls) * 100 if len(pnls) > 0 else 0.0
                
                gw = sum(p for p in pnls if p > 0)
                gl = abs(sum(p for p in pnls if p < 0))
                pf = gw / gl if gl > 0 else float("inf")
                
                pnl_usd = bal - INITIAL_BAL
                
                ex_str = " ".join(f"{k}={v}" for k, v in ex.items() if v > 0)
                l2_lbl = f"{l2_stop*100:.1f}%" if l2_stop else "None"
                mtm_lbl = f"{mtm_stop*100:.1f}%" if mtm_stop else "None"
                
                results.append((pnl_usd, l2_lbl, mtm_lbl, tp_dca, len(tr), win_pct, pf, ex_str))
                
                # Print baseline and best candidates in real time
                if l2_stop is None and mtm_stop is None and tp_dca == 0.0100:
                    print(f"{l2_lbl:<10} {mtm_lbl:<10} {tp_dca*100:>6.2f}% {len(tr):>7} {win_pct:>5.1f}% {pf:>6.2f} {pnl_usd:>+12,.2f}  [DEPLOYED BASELINE] {ex_str}")
                elif pnl_usd > -3000: # print anything that survives better
                    print(f"{l2_lbl:<10} {mtm_lbl:<10} {tp_dca*100:>6.2f}% {len(tr):>7} {win_pct:>5.1f}% {pf:>6.2f} {pnl_usd:>+12,.2f}  {ex_str}")
                    
    # Show top 5 sorted by P&L
    print("\nTop 5 parameter combinations:")
    results_sorted = sorted(results, key=lambda x: -x[0])
    for r in results_sorted[:5]:
        print(f"L2 Stop: {r[1]:<6} | MTM: {r[2]:<6} | TP DCA: {r[3]*100:.2f}% | Trades: {r[4]} | Win%: {r[5]:.1f}% | PF: {r[6]:.2f} | PnL: ${r[0]:+,.2f} | Exits: {r[7]}")

if __name__ == "__main__":
    main()
