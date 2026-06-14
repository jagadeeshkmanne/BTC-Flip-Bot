# v3 — 4h Trend Portfolio (long/flat, multi-pair perp)

**Status:** validated in backtest (session winner), paper bot REMOVED from the
fleet 2026-06-14 per user request. This file preserves the strategy so it can be
rebuilt. Original bot code: `git show a0c7b62:bot/bot_v3_trend.py`.

## The rule

Per coin, evaluated on **CLOSED 4h bars**:

```
LONG  when  EMA30 > EMA150  AND  close > EMA50  AND  ADX14 > 20
FLAT  otherwise
```

- **Leader gate:** alts (ETH/SOL/BNB) additionally require **BTC** to be in the
  same LONG state. If BTC is flat, the alts stay flat regardless of their own signal.
- **No shorts** (9 independent short tests all failed). **No DCA, no grid.**

## Universe & sizing

| Param | Value |
|---|---|
| Pairs | BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT (USDT perps) |
| Timeframe | 4h (decisions on closed bars only) |
| EMA fast / slow / exit | 30 / 150 / 50 |
| ADX length / min | 14 / 20.0 |
| Leverage | 2x cross (never liquidated in 7y of history) |
| Sizing | equal weight — per-coin margin = equity / 4, notional = margin × 2 |
| Catastrophe stop | live price ≤ entry × (1 − 8%) → close at LIVE price, any tick |

## Exits

- **Signal-off:** when any LONG condition fails on a closed 4h bar → exit at market.
- **Catastrophe stop:** the only intra-bar action — 8% hard stop from entry,
  booked at live price (flash-crash bound).

## Costs (honest accounting)

- Perp taker fee 0.055% + slippage 0.02% per side, on notional.
- Funding charged on held positions at each 8h funding event (actual Bybit rate;
  longs pay positive funding).

## Validation (4h, 2019–2026, honest engine, funding + fees)

- **OOS (2023→): +262% / Sharpe 1.82 / maxDD −14% at 1x**; @2x +1007% / −28%.
- Parameter plateau: 54-cell sweep, **all OOS-positive** (edge is structural, not
  a single tuned cell).
- Went FLAT through COVID, May-2021, LUNA, FTX crashes (the trend filter exited
  before the worst of each).
- Worst simultaneous 4h bar while long: −17% of equity at 1x.

## Notes / caveats

- 5x and 3x leverage = wipeout (volatility drag; 5x made LESS than 3x). 2x is the
  validated ceiling.
- ETH flash-crashes like an alt; the leader gate is what protects the alt sleeves.
- Intended to be forward-validated ~4–8 weeks before any real money. The paper bot
  removal does NOT invalidate the backtest — only the live forward-test was stopped
  (it had taken 0 trades).
