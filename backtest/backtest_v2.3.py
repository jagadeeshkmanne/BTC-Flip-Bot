import numpy as np
import pandas as pd
import os

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_1h.csv"
INITIAL_BAL = 5000.0
PER_LEG_NOTIONAL = INITIAL_BAL * 0.95 * 5.0 / 2.0   # 5x leverage, 2 legs
RSI_LONG, RSI_SHORT = 35, 65
DCA_SPACING = 0.010
TP_SINGLE = 0.010
TP_DCA = 0.010
SL_PCT = 0.015
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
    df["rsi"] = wilder_rsi(df["close"], 14)
    df["ema_fast"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=200, adjust=False).mean()
    
    # Trend state: 1 for UP, -1 for DOWN
    df["trend"] = np.where(df["ema_fast"] > df["ema_slow"], 1.0, -1.0)
    
    # We delay the indicators by 1 bar to simulate what the bot sees at bar open
    df["rsi_prev"] = df["rsi"].shift(1)
    df["trend_prev"] = df["trend"].shift(1)
    
    return df.dropna(subset=["rsi_prev", "trend_prev"])

def run_backtest(df):
    ts = df["timestamp"].values
    O, H, L, C = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    RSI = df["rsi_prev"].values
    TREND = df["trend_prev"].values
    n = len(df)
    
    balance = INITIAL_BAL
    pos = None
    trades = []
    
    for i in range(n):
        o, h, l, c = O[i], H[i], L[i], C[i]
        
        if pos is None:
            # Check for entries at the open of the current bar
            rsi_val = RSI[i]
            trend_val = TREND[i]
            side = None
            
            if rsi_val <= RSI_LONG and trend_val == 1.0:
                side = 1
            elif rsi_val >= RSI_SHORT and trend_val == -1.0:
                side = -1
                
            if side is not None:
                eff = o * (1 + SLIPPAGE * side)
                qty = PER_LEG_NOTIONAL / eff
                pos = {
                    "side": side, "avg": eff, "worst": eff, "qty": qty,
                    "legs": 1, "fees_in": eff * qty * FEES_TAKER
                }
                
        if pos is not None:
            side = pos["side"]
            
            # Check DCA
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
                    
            avg = pos["avg"]
            
            # Standard Exits
            tp_pct = TP_SINGLE if pos["legs"] == 1 else TP_DCA
            tp_px = avg * (1 + tp_pct * side)
            sl_px = pos["worst"] * (1 - SL_PCT * side)
            
            tp_hit = (h >= tp_px) if side == 1 else (l <= tp_px)
            sl_hit = (l <= sl_px) if side == 1 else (h >= sl_px)
            
            if sl_hit:
                exit_px = min(sl_px, o) if side == 1 else max(sl_px, o)
                eff_exit = exit_px * (1 - SLIPPAGE * side)
                gross = (eff_exit - avg) * pos["qty"] * side
                net = gross - eff_exit * pos["qty"] * FEES_TAKER - pos["fees_in"]
                balance += net
                trades.append((net, "SL"))
                pos = None
            elif tp_hit:
                exit_px = max(tp_px, o) if side == 1 else min(tp_px, o)
                eff_exit = exit_px * (1 - SLIPPAGE * side)
                gross = (eff_exit - avg) * pos["qty"] * side
                net = gross - eff_exit * pos["qty"] * FEES_TAKER - pos["fees_in"]
                balance += net
                trades.append((net, "TP"))
                pos = None
                
    return balance, trades

def main():
    print("Preparing 1H Backtest for v2.3...")
    df = prep()
    bal, tr = run_backtest(df)
    
    pnls = [t[0] for t in tr]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    
    win_rate = len(wins) / len(pnls) * 100 if len(pnls) > 0 else 0
    pf = sum(wins) / abs(sum(losses)) if sum(losses) != 0 else float("inf")
    pnl_usd = bal - INITIAL_BAL
    
    print("\n--- v2.3 BACKTEST RESULTS (Honest Fills + Taker Fees) ---")
    print(f"Timeframe:      1-Hour")
    print(f"Trades:         {len(tr)}")
    print(f"Win Rate:       {win_rate:.1f}%")
    print(f"Profit Factor:  {pf:.2f}")
    print(f"Total PnL:      ${pnl_usd:+,.2f} (from $5000)")
    
    exits = {}
    for t in tr:
        exits[t[1]] = exits.get(t[1], 0) + 1
    print(f"Exits:          {exits}")

if __name__ == "__main__":
    main()
