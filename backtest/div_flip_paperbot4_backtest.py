"""45-day (or longer) backtest with full DCA + range filter + conviction sizing.

Replays divflip signals through a position-state machine that mimics the live
paper bot's behavior (DCA fills, worst-anchored SL, BE+trail, partial TP).
Tests multiple candidate configs side-by-side."""
from __future__ import annotations
import sys, datetime as dt
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import v22_backtest as bt
from div_flip_backtest import detect_divergences

INITIAL = 5000.0
LEVERAGE = 3.0
COMM = 0.0004


def attach_range_pos(df, bars=288):
    rng_high = df["high"].rolling(bars, min_periods=50).max()
    rng_low = df["low"].rolling(bars, min_periods=50).min()
    df = df.copy()
    df["range_pos"] = (df["close"] - rng_low) / (rng_high - rng_low).replace(0, np.nan) * 100
    return df


def conviction(side, rp, mode, floor=0.5):
    """Returns size factor 0..1. Returns 0 to SKIP."""
    if pd.isna(rp): return 1.0
    if mode == "none": return 1.0
    if mode == "binary":
        if side == "LONG" and rp <= 50: return 1.0
        if side == "SHORT" and rp >= 50: return 1.0
        return 0.0
    if mode == "conviction":
        if side == "LONG":
            if rp > 50: return 0.0
            c = (50 - rp) / 50
        else:
            if rp < 50: return 0.0
            c = (rp - 50) / 50
        c = max(0.0, min(1.0, c))
        return floor + (1 - floor) * c
    return 1.0


def run(df, bull, bear, cfg):
    """State machine. cfg keys:
       dca_count, dca_spacing, weights, sl_pct, tp_pct, be_trigger,
       trail_buffer, filter_mode, conv_floor
    """
    equity = INITIAL; peak = INITIAL; max_dd = 0.0
    trades = []
    in_pos = False
    side = ""; n_levels = cfg["dca_count"]; weights = list(cfg["weights"])
    sum_w = 0.0
    trig_pxs = []; filled = []  # parallel arrays
    avg_px = 0.0; worst_px = 0.0
    base_qty = 0.0  # = total qty target (sum of weights worth)
    be_activated = False
    trail_peak = 0.0
    half_taken = False
    entry_time = ""

    def close_full(j, fill, reason):
        nonlocal equity, in_pos, peak, max_dd
        # Use total filled qty
        fq = sum(filled)
        if fq <= 0:
            in_pos = False; return
        pnl = (fill - avg_px) * fq if side == "LONG" else (avg_px - fill) * fq
        pnl -= fill * fq * COMM
        equity += pnl
        trades.append({"entry_time": entry_time, "side": side, "exit_px": fill,
                       "qty": fq, "pnl_usd": pnl, "reason": reason, "avg": avg_px})
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100
        if dd > max_dd: max_dd = dd
        in_pos = False

    def close_partial(j, fill, qty, reason):
        nonlocal equity, peak, max_dd
        pnl = (fill - avg_px) * qty if side == "LONG" else (avg_px - fill) * qty
        pnl -= fill * qty * COMM
        equity += pnl
        # Reduce filled proportionally
        share = qty / sum(filled)
        for k in range(len(filled)):
            filled[k] *= (1 - share)
        trades.append({"entry_time": entry_time, "side": side, "exit_px": fill,
                       "qty": qty, "pnl_usd": pnl, "reason": reason, "avg": avg_px})
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100
        if dd > max_dd: max_dd = dd

    def open_pos(j, direction, conv_size):
        nonlocal in_pos, side, trig_pxs, filled, avg_px, worst_px, sum_w, base_qty, \
                 be_activated, trail_peak, half_taken, entry_time, equity
        c = df.at[j, "close"]
        # Position notional: equity * lev * conv_size, divided by L1 weight share
        total_w = sum(weights)
        # Compute trigger prices
        trig_pxs = [c]
        for k in range(n_levels - 1):
            nxt = trig_pxs[-1] * (1 - cfg["dca_spacing"]) if direction == "LONG" \
                  else trig_pxs[-1] * (1 + cfg["dca_spacing"])
            trig_pxs.append(nxt)
        # L1 fills now at c
        filled = [0.0] * n_levels
        # Size: total notional = equity * leverage * conv_size
        # → base qty for FULL fill = (equity * lev * conv_size) / avg_price (≈ c)
        # → L1 qty = base * (w0 / total_w)
        base_qty = (equity * LEVERAGE * conv_size) / c
        l1_qty = base_qty * (weights[0] / total_w)
        if l1_qty <= 0: return
        filled[0] = l1_qty
        sum_w = weights[0]
        equity -= c * l1_qty * COMM
        in_pos = True; side = direction
        avg_px = c
        worst_px = c
        be_activated = False
        trail_peak = c
        half_taken = False
        entry_time = df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")

    for j in range(len(df)):
        C = df.at[j, "close"]; H = df.at[j, "high"]; L_ = df.at[j, "low"]
        if in_pos:
            # 1) Fill any DCAs reached this bar
            for k in range(1, n_levels):
                if filled[k] == 0:
                    trig = trig_pxs[k]
                    if (side == "LONG" and L_ <= trig) or (side == "SHORT" and H >= trig):
                        leg_qty = base_qty * (weights[k] / sum(weights))
                        filled[k] = leg_qty
                        equity -= trig * leg_qty * COMM
                        # Recompute avg using filled fills only
                        total_qty = sum(filled)
                        total_cost = sum(trig_pxs[m] * filled[m] for m in range(n_levels))
                        avg_px = total_cost / total_qty
                        worst_px = trig if (side == "LONG" and trig < worst_px) or \
                                          (side == "SHORT" and trig > worst_px) else worst_px

            # 2) SL: anchored to worst level we've FILLED or PROJECTED (use trig of deepest planned)
            worst_anchor = trig_pxs[-1]  # project to deepest planned DCA
            sl_px = worst_anchor * (1 - cfg["sl_pct"]) if side == "LONG" \
                   else worst_anchor * (1 + cfg["sl_pct"])
            if (side == "LONG" and L_ <= sl_px) or (side == "SHORT" and H >= sl_px):
                close_full(j, sl_px, "SL"); continue

            # 3) Favorable move
            fav_px = H if side == "LONG" else L_
            fav_pct = ((fav_px - avg_px) / avg_px) if side == "LONG" else ((avg_px - fav_px) / avg_px)

            # 4) BE activation
            if not be_activated and fav_pct >= cfg["be_trigger"]:
                be_activated = True
                trail_peak = fav_px
            if be_activated:
                if side == "LONG":
                    trail_peak = max(trail_peak, fav_px)
                    trail_sl = max(trail_peak * (1 - cfg["trail_buffer"]), avg_px)
                    if L_ <= trail_sl:
                        close_full(j, trail_sl, "TRAIL"); continue
                else:
                    trail_peak = min(trail_peak, fav_px)
                    trail_sl = min(trail_peak * (1 + cfg["trail_buffer"]), avg_px)
                    if H >= trail_sl:
                        close_full(j, trail_sl, "TRAIL"); continue

            # 5) TP
            tp_px = avg_px * (1 + cfg["tp_pct"]) if side == "LONG" \
                   else avg_px * (1 - cfg["tp_pct"])
            if (side == "LONG" and H >= tp_px) or (side == "SHORT" and L_ <= tp_px):
                close_full(j, tp_px, "TP"); continue

            # 6) FLIP on opposite signal
            if side == "LONG" and bear[j]:
                close_full(j, C, "FLIP")
                rp = df.at[j, "range_pos"]
                cs = conviction("SHORT", rp, cfg["filter_mode"], cfg["conv_floor"])
                if cs > 0:
                    open_pos(j, "SHORT", cs)
                continue
            if side == "SHORT" and bull[j]:
                close_full(j, C, "FLIP")
                rp = df.at[j, "range_pos"]
                cs = conviction("LONG", rp, cfg["filter_mode"], cfg["conv_floor"])
                if cs > 0:
                    open_pos(j, "LONG", cs)
                continue

        if not in_pos:
            if bull[j] or bear[j]:
                direction = "LONG" if bull[j] else "SHORT"
                rp = df.at[j, "range_pos"]
                cs = conviction(direction, rp, cfg["filter_mode"], cfg["conv_floor"])
                if cs > 0:
                    open_pos(j, direction, cs)

    return trades, equity, max_dd


def summarize(trades, equity, max_dd):
    if not trades:
        return {"trades": 0, "equity": equity, "pct": 0.0, "wr": 0.0, "pf": 0.0,
                "dd": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "n_cycles": 0}
    dft = pd.DataFrame(trades)
    # Cycle by entry_time+side (DCAs/partial TPs share entry_time)
    cycles = dft.groupby(["entry_time","side"], sort=False).agg(
        cycle_pnl=("pnl_usd","sum")).reset_index()
    n = len(cycles)
    wins = cycles[cycles["cycle_pnl"] > 0]
    losses = cycles[cycles["cycle_pnl"] <= 0]
    gw = wins["cycle_pnl"].sum()
    gl = -losses["cycle_pnl"].sum()
    pf = gw / gl if gl > 0 else float("inf")
    return {"trades": int(n), "equity": equity, "pct": (equity/INITIAL - 1) * 100,
            "wr": len(wins)/n*100 if n else 0, "pf": pf, "dd": max_dd,
            "avg_win": gw/len(wins) if len(wins) else 0,
            "avg_loss": gl/len(losses) if len(losses) else 0,
            "n_cycles": n}


def main():
    start = "2026-04-08"; end = "2026-05-23"
    if len(sys.argv) >= 2: start = sys.argv[1]
    if len(sys.argv) >= 3: end = sys.argv[2]

    print(f"Loading 5m data {start} → {end}...", flush=True)
    df = bt.load_5m(start, end)
    if df.empty:
        print("no data"); return
    print(f"  {len(df)} bars")
    df = attach_range_pos(df, 288)  # 24h range
    print("Detecting divergences...", flush=True)
    bull, bear = detect_divergences(df)
    print(f"  bull signals: {bull.sum()}, bear: {bear.sum()}")

    # Configs to test
    configs = [
        # baseline: v1 actual
        ("v1 actual (3 DCA 0.35% 3:4:1.5, TP1%, BE0.55%)",
         dict(dca_count=3, dca_spacing=0.0035, weights=[3,4,1.5], sl_pct=0.01,
              tp_pct=0.01, be_trigger=0.0055, trail_buffer=0.003,
              filter_mode="none", conv_floor=0.5)),
        # range filter only (binary)
        ("+ range filter (binary, 3 DCA same)",
         dict(dca_count=3, dca_spacing=0.0035, weights=[3,4,1.5], sl_pct=0.01,
              tp_pct=0.01, be_trigger=0.0055, trail_buffer=0.003,
              filter_mode="binary", conv_floor=0.5)),
        # remove L3 (2 DCA)
        ("+ range + 2 DCA 0.35% 3:4",
         dict(dca_count=2, dca_spacing=0.0035, weights=[3,4], sl_pct=0.01,
              tp_pct=0.01, be_trigger=0.0055, trail_buffer=0.003,
              filter_mode="binary", conv_floor=0.5)),
        ("+ range + 2 DCA 0.5% 3:4",
         dict(dca_count=2, dca_spacing=0.005, weights=[3,4], sl_pct=0.01,
              tp_pct=0.01, be_trigger=0.0055, trail_buffer=0.003,
              filter_mode="binary", conv_floor=0.5)),
        # better TP / late trail
        ("+ range + 2 DCA 0.5% + TP 1.5%",
         dict(dca_count=2, dca_spacing=0.005, weights=[3,4], sl_pct=0.01,
              tp_pct=0.015, be_trigger=0.0055, trail_buffer=0.003,
              filter_mode="binary", conv_floor=0.5)),
        ("+ range + 2 DCA 0.5% + TP 1.5% + late trail",
         dict(dca_count=2, dca_spacing=0.005, weights=[3,4], sl_pct=0.01,
              tp_pct=0.015, be_trigger=0.01, trail_buffer=0.005,
              filter_mode="binary", conv_floor=0.5)),
        # conviction sizing layered on best
        ("BEST + CONVICTION floor 0.3",
         dict(dca_count=2, dca_spacing=0.005, weights=[3,4], sl_pct=0.01,
              tp_pct=0.015, be_trigger=0.01, trail_buffer=0.005,
              filter_mode="conviction", conv_floor=0.3)),
        ("BEST + CONVICTION floor 0.5",
         dict(dca_count=2, dca_spacing=0.005, weights=[3,4], sl_pct=0.01,
              tp_pct=0.015, be_trigger=0.01, trail_buffer=0.005,
              filter_mode="conviction", conv_floor=0.5)),
        ("BEST + CONVICTION floor 0.7",
         dict(dca_count=2, dca_spacing=0.005, weights=[3,4], sl_pct=0.01,
              tp_pct=0.015, be_trigger=0.01, trail_buffer=0.005,
              filter_mode="conviction", conv_floor=0.7)),
        # 0 DCA variant
        ("+ range + 0 DCA (1 entry) + TP 1.5% + late trail",
         dict(dca_count=1, dca_spacing=0.005, weights=[1], sl_pct=0.01,
              tp_pct=0.015, be_trigger=0.01, trail_buffer=0.005,
              filter_mode="binary", conv_floor=0.5)),
    ]

    print(f"\n{'config':<55} {'cycles':>6} {'$net':>8} {'pct':>7} {'WR':>5} {'PF':>6} {'DD':>5} {'avgW':>6} {'avgL':>6}")
    print("-"*115)
    for label, cfg in configs:
        trades, equity, max_dd = run(df, bull, bear, cfg)
        s = summarize(trades, equity, max_dd)
        net = equity - INITIAL
        print(f"{label:<55} {s['n_cycles']:>6d} ${net:>+7.0f} {s['pct']:>+6.1f}% {s['wr']:>4.0f}% {s['pf']:>6.2f} {s['dd']:>4.1f}% {s['avg_win']:>5.0f}$ {s['avg_loss']:>5.0f}$")


if __name__ == "__main__":
    main()
