# V1 Bot Strategy — Full Spec for External Review

Generated 2026-06-06 — share this with other agents/quants for verification.

---

## STRATEGY SUMMARY

**Name:** RSI-Scalp ULTIMATE (v1)
**Market:** BTCUSDT perpetual futures (Bybit), 5-minute bars
**Type:** Mean-reversion fade with DCA + break-even-after-DCA risk management
**Leverage:** 3× (with weekend 2× position-size multiplier)
**Starting balance:** $5,000 paper

### CORE LOGIC

```
ENTRY: All filters below must pass simultaneously
  1. RSI(9) on 5m bars ≤ 30 (LONG) or ≥ 70 (SHORT)
  2. 15m EMA20 > EMA50 trend gate
     LONG only when trend = UP
     SHORT only when trend = DOWN
  3. GAP firmness: |EMA20 - EMA50| / EMA50 ≥ 0.25%
     Skips knife-edge trends where EMAs are nearly identical
  4. ATR(14) on 5m / close < 0.60% (skip high-volatility chop)
  5. All indicators must be available (defensive fail-closed)

POSITION SIZING:
  qty_per_leg = balance × 0.95 × leverage × weekend_mult / price / DCA_LEVELS
  - leverage = 3.0
  - DCA_LEVELS = 2 (split capital across L1 + L2)
  - weekend_mult = 2.0 (Sat/Sun) else 1.0

DCA (DOLLAR-COST AVERAGING):
  L1 fills at entry signal (next bar open)
  L2 fills at 0.5% adverse from L1 (worst_entry × 1.005 for SHORT)
  When L2 fills: position size doubles, avg becomes midpoint(L1, L2)

EXITS (checked each tick, in priority order):
  1. SL hit
     If L1 only: SL at worst × (1 + 0.6%) for SHORT
     If L2 filled: SL at avg (BREAK-EVEN — caps catastrophic losses)
  2. TP hit
     If L1 only: TP at avg × (1 - 0.5%) for SHORT — single-leg target
     If L2 filled: TP at avg × (1 - 0.25%) — tighter target after averaging
  3. Trend-flip exit
     If 15m trend flips against position direction, exit at close
  4. DCA fill (if L2 not yet filled and dca_px hit)

ADDITIONAL RISK CONTROLS:
  - Post-trade cooldown: 3 bars (15 min) before next entry
  - After-loss circuit breaker: 1 loss → 15 min pause
  - Daily max loss: $200 net → pauses all entries until next UTC day
  - Atomic state writes (temp file + os.replace)
```

### 5-YEAR FAITHFUL BACKTEST RESULTS
(No-lookahead methodology: HTF bars labeled at CLOSE time, see `backtest/v11_faithful_backtest.py`)

```
Period: 2021-06-07 → 2026-06-06 (5 years, 525,540 × 5m bars)
Costs:  0.055% taker commission per side, 2 bps slippage assumed

Trades:        3,187
Win rate:      54.2%
Total return:  +205.6%
Max drawdown:  -9.9%
Profit factor: 1.31
Sharpe ratio:  1.81
CAGR:          +25.0%

Average win:   $25.10 weekday / $50 weekend
Average loss:  -$22.10
Max single loss: -$101
```

### KEY MECHANISTIC INSIGHT

The "BE-after-DCA" exit is what makes this strategy work:
- When L2 fires (position doubled, currently underwater), SL moves to avg entry
- If price reverses to avg: exit at $0 (just fees, ~$0-5 loss)
- If price reverses past avg to TP: WIN on doubled position
- If price keeps moving adverse past avg: BE-DCA fires, exit at ~$0

This turns the asymmetric pain of "DCA into a runaway move" into a near-zero outcome,
while preserving the asymmetric gain of "DCA into a reversal" (doubled qty × TP).

---

## PRODUCTION CODE

### `strategies/day/core_rsiscalp.py` (shared constants + helpers)

```python
# Shared between bot variants. See file for full source.
LEVERAGE = 3.0
RSI_PERIOD = 9
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
DCA_LEVELS = 2
DCA_SPACING = 0.005       # 0.5%
TP_PCT_SINGLE = 0.005     # 0.50%
TP_PCT_DCA = 0.0025       # 0.25%
SL_FROM_WORST = 0.01      # 1.0% (overridden to 0.006 in v1)
BREAKER_LOSSES = 1        # 1 loss triggers...
BREAKER_PAUSE_HOURS = 0.25  # ...15 min pause
```

### `strategies/day/bot_rsiscalp.py` (v1 production bot)

See full file (603 lines): `/Users/jags/Desktop/BTC-Flip-Bot/strategies/day/bot_rsiscalp.py`

Key overrides from core defaults:
```python
SL_FROM_WORST = 0.006              # 0.6% (tightened from core's 1%)
USE_BE_AFTER_DCA = True            # the critical risk-management feature
USE_TREND_FLIP_EXIT = True
WEEKEND_QTY_MULT = 2.0
DAILY_MAX_LOSS = 200.0
BLOCKED_HOURS = set()              # empty by default (dropped during live audit)
RSISCALP_ATR_MAX_PCT = 0.60        # chop filter
RSISCALP_1H_MOVE_MAX_PCT = 100.0   # effectively off (dropped during live audit)
TREND_GAP_MIN = 0.0025             # 0.25% GAP filter
```

### `backtest/v11_faithful_backtest.py` (no-lookahead reference)

This is the NO-LOOKAHEAD reference backtest. Critical for honest review:
- 15m bars labeled at CLOSE time (not OPEN) — `df15["timestamp"] += 15min`
- 1h bars labeled at CLOSE time — `df1h["timestamp"] += 1hr`
- `merge_asof(direction="backward")` then correctly returns only CLOSED bars
- Bar fill order: SL → TP → trend-flip → DCA (pessimistic on bars spanning multiple levels)
- Commission: 0.055%/side; can be adjusted

Located at: `/Users/jags/Desktop/BTC-Flip-Bot/backtest/v11_faithful_backtest.py`

---

## KNOWN BUGS WE FOUND AND FIXED THIS SESSION

1. **Lookahead in prior backtests**: Earlier scripts used `merge_asof(backward)` on HTF
   bars labeled at OPEN time → 5m query at time T returned 15m/1h bar that hadn't
   closed yet. Inflated +103%/6mo and +2,023%/5yr claims to fake values.
   FIX: Label HTF bars at CLOSE time (add bar duration to timestamp).

2. **DCA L2 ignored weekend 2× multiplier**: Original maybe_dca() recalculated qty
   from balance, ignoring whether L1 was sized 2× for weekend.
   FIX: Store `weekend_2x` flag on pos, apply same multiplier on L2.

3. **Inflated profit-lock backtest**: A "BE-plus" SL variant showed +130% CAGR. Bug
   was the SL price check used `high >= sl_px` for SHORT, but BE-plus SL is BELOW avg,
   so high was already above it → SL "fired" immediately at phantom profit.
   FIX: Check whether the bar's price ACTUALLY CROSSED the lock level (low ≤ lock_px
   AND high ≥ lock_px).

---

## WHAT TO ASK OTHER AGENTS TO VERIFY

1. **Reproduce the +205%/-9.9% baseline** on the same 5y BTCUSDT 5m data with the
   same fee model. Within ±10% of these numbers = healthy.

2. **Audit for lookahead bias.** Specifically check `merge_asof` calls and any
   indicator computed using FUTURE bars.

3. **Stress-test fees.** What happens at 0.04%, 0.055%, 0.06%, 0.10%/side?
   If the strategy collapses at 0.10%, that tells us how much fee headroom exists.

4. **Out-of-sample test.** Train (just observe) 2021-2024, test 2025-2026.
   Does the +25% CAGR hold OOS?

5. **Bar fill model.** When a bar's range spans both SL and TP, my model assigns SL
   (pessimistic). Some backtesters assign TP. Run both and compare.

6. **Daily max loss circuit breaker.** Does the strategy depend on this for surviving
   2024 (the only losing year in my baseline)? Run with it disabled.

---

## CURRENT LIVE STATUS

- Deployed on: GCP VM `btc-bot-eu` (paper-only)
- Dashboard: http://34.14.124.215:8888/
- First live trade closed today (2026-06-06): SHORT $61,140 → $60,834, +$65.56 in 39 min
- Bot state stored at `data/paper_rsiscalp_trend/state.json` (atomic writes)
- Cron tick: every 1 minute

---

End of spec.
