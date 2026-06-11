# BTC-Flip-Bot — Established Findings (read before analyzing anything)

Updated 2026-06-11. Every claim below is backed by an honest backtest
(no lookahead, honest stop fills, real fees). Do NOT trust any dashboard
history or backtest result predating 2026-06-11 — see finding #1.

## 1. The stop-fill booking artifact (CRITICAL context)

Until 2026-06-11 the paper bots and all backtests booked stop-family exits
(SL / BE-DCA / L2_TRAIL) AT the stop price even when the market was already
beyond it. BE-DCA exits were recorded as $0 "neutrals" — they are real
losses. This fiction was the ENTIRE apparent edge of the v2.x bots
(fictional: +$884K/5y; honest: PF ~1.0 at zero fees, -100% with fees).
Fixed in bot_rsiscalp_v3.py 2026-06-11 (exits book live price, real fees
charged, histories archived and reset). Any old number you see — dashboards,
memory of "+6.63%", old backtests — predates the fix and is invalid.

## 2. The 5m entries carry zero information — measured seven ways

The live entries (RSI9 35/65 counter-trend + 15m EMA gap + ATR gates) were
tested under SEVEN exit families: BE+trail (live design), plain stop, resting
limit at avg, fixed TP/SL (5 geometries), signal exits, martingale, and S/R-
based exits. Win rates ranged 8%-78%; zero-fee PF stayed 0.92-1.01 in ALL of
them, matching the random-walk barrier formula WR = SL/(TP+SL) within ~1pt.
Conclusion: exits cannot fix these entries. No further 5m exit/R:R/wrapper
ideas are worth testing.

## 3. Wrappers don't create edge — proven by identity and simulation

Hedge ("open L+S, close winner"), martingale, grid, DCA are exposure-path
wrappers: P&L = exposure x price moves - fills x costs. Hedge-cycle sim:
+$330 realized "wins" mirrored by -$329 in open legs (net = -fees).
Martingale 2x at 5x lev: 99.5% WR, liquidated in 16 days (2021-05-19).
Commercial bots (3commas/Pionex/Tafabot/Bybit bot cards) showing 100% win
rates use realized-only accounting — same fiction as #1.

## 4. Timeframe gradient — the only edge is at daily scale

Structure-break system (EMA50 bias + N-day breakout + ratchet trail, SL-flip)
tested at every timeframe, real fees, 2019/20-2026:
  5m/15m/1H fast: PF 0.88-1.13 (dead) | 4H: 1.01-1.24 (marginal)
  1D: PF 1.35-1.85 full-sample, beats buy-hold with half the DD, +2022 bear.
Sub-daily cells only survive with multi-day lookbacks (= daily in disguise).
WALK-FORWARD (fit 2019-23, blind 2024-26): MARGINAL — fit-selected cell OOS
PF 1.05; only slowest cells (N>=55) clearly positive OOS (PF 1.16-1.70).
Edge is real, small, decaying. Deploy slow + small or not at all.

## 5. What is actually real

- Funding harvest: long spot + 1x perp short, income = funding (~8% APR at
  2026-06-11 rates, paid 8-hourly). External cash flow, no prediction,
  no liquidation at 1x. The only income here that isn't a backtest.
- Slow daily structure-break (N>=55) as a small satellite (see #4).
- Realistic total on $5K: roughly $30-60/month. Anything promising more
  has so far always contained a fill fiction or fee blindness.

## 6. Live state (as of 2026-06-11)

v2.1 + v2.2 paper bots on GCP btc-bot-eu (cron, 1-min ticks) run with honest
booking, 0.055% fees, SL env fixed, fresh $5K states (old histories archived
as data/v2.*/state_fictional_backup_20260611_1318.json). They are a 2-week
truth experiment, expected to bleed slowly per #2. Paper still omits
slippage, funding, intra-minute moves — reads slightly BETTER than real.

## File map for analysis

- bot/bot_rsiscalp_v3.py + bot/core_rsiscalp.py — live strategy (env
  overrides in scripts/run_v2.*.sh take precedence over code defaults!)
- Honest harness + all experiment scripts: git commit 5cad355
  (e.g. `git show 5cad355:backtest/live_faithful.py`)
- Data: data/cache/BTCUSDT_{5m,15m,1h,4h,1d}.csv
- TradingView port: backtest/rsiscalp_v6.pine (LEGACY vs HONEST fill modes)

## Checklist for any NEW backtest (non-negotiable)

1. Signals on closed bars only; fill next bar open.
2. Stop fills = worse(stop price, bar open). Never book a stop at its own
   price when the bar opens beyond it.
3. Same-bar DCA-fill + exit: defer exit (no wick-order lookahead);
   TP+SL same bar -> take the SL (pessimistic).
4. Real fees both sides (taker 0.055%) + slippage on market fills.
5. Track mark-to-market equity and liquidation, not just closed balance.
6. Year-wise results + walk-forward before believing any total.
