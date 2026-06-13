import re

with open("/Users/jags/Desktop/BTC-Flip-Bot/bot/bot_v2_3.py", "r") as f:
    content = f.read()

# 1. Remove the rsi_signal override
content = re.sub(
    r"# Override rsi_signal to use our.*?def rsi_signal\(rsi_val\):.*?(?:return None\n)",
    "",
    content,
    flags=re.DOTALL
)

# 2. Modify the trend fetching and sig calculation in main()
old_logic = """    sig = rsi_signal(rsi_val)

    # ── Optional 15m trend gate (entry only) ──
    trend = None  # "UP" / "DOWN" / None
    trend_gap_pct = None  # signed gap %: (EMA20 - EMA50) / EMA50 × 100
    # 2026-06-12 FIX (latent): counter-trend mode also needs trend data — its
    # defensive gate blocks entries when trend is None, so TREND=0 +
    # COUNTER_TREND=1 froze entries forever. Not hit in production (both run
    # scripts set RSISCALP_TREND=1), fixed for config safety.
    if USE_TREND_FILTER or USE_COUNTER_TREND:
        df_tf = fetch_klines(TREND_TF, 300)
        if df_tf is not None and len(df_tf) >= TREND_EMA_SLOW:
            ema_f = df_tf["close"].ewm(span=TREND_EMA_FAST, adjust=False).mean()
            ema_s = df_tf["close"].ewm(span=TREND_EMA_SLOW, adjust=False).mean()
            ema_f_v = float(ema_f.iloc[-2])
            ema_s_v = float(ema_s.iloc[-2])
            trend = "UP" if ema_f_v > ema_s_v else "DOWN"
            trend_gap_pct = (ema_f_v - ema_s_v) / ema_s_v * 100.0
            # v3 fix 2026-06-08: stash trend for open_position() snapshot.
            state["_last_trend"] = trend
        else:
            log.warning(f"  {TREND_TF} trend: insufficient data — gate inactive this tick")"""

new_logic = """    # ── 1h trend gate (entry only) ──
    trend = None  # "UP" / "DOWN" / None
    trend_state = None # 1.0 or -1.0
    trend_gap_pct = None  # signed gap %
    
    df_tf = fetch_klines(TREND_TF, 300)
    if df_tf is not None and len(df_tf) >= TREND_EMA_SLOW:
        ema_f = df_tf["close"].ewm(span=TREND_EMA_FAST, adjust=False).mean()
        ema_s = df_tf["close"].ewm(span=TREND_EMA_SLOW, adjust=False).mean()
        ema_f_v = float(ema_f.iloc[-2])
        ema_s_v = float(ema_s.iloc[-2])
        trend = "UP" if ema_f_v > ema_s_v else "DOWN"
        trend_state = 1.0 if trend == "UP" else -1.0
        trend_gap_pct = (ema_f_v - ema_s_v) / ema_s_v * 100.0
        state["_last_trend"] = trend
    else:
        log.warning(f"  {TREND_TF} trend: insufficient data")

    # Evaluate signal with trend_state (v2.3 requires trend)
    sig = rsi_signal(rsi_val, trend_state)
"""
content = content.replace(old_logic, new_logic)

with open("/Users/jags/Desktop/BTC-Flip-Bot/bot/bot_v2_3.py", "w") as f:
    f.write(content)
