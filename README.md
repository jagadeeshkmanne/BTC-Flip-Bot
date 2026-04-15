# BTC Flip Bot — Strategy V5

Multi-timeframe candlestick flip bot for Binance Futures. Trades BTCUSDT perp on 1H execution
with Daily + 4H trend filters. Runs on GCP free tier (~$0/month).

**Honest 5yr backtest** ($10K start, taker fees + slippage, 2× lev):

| Metric | Value |
|---|---|
| Final | **$155,646** (+1,456%) |
| CAGR | **+73.2%** |
| Max drawdown | **-21.8%** |
| Profit factor | **4.46** |
| Win rate | **41.7%** |
| Trades | 60 (~12/yr) |
| Years positive | **6 of 6** |

At 3× leverage: $10K → ~$379K (+107% CAGR / -27% DD).
At $5K start (2× lev): $5K → ~$78K.

---

## Strategy Logic

### Entry — all conditions must align

| Timeframe | Condition |
|---|---|
| **Daily** | Close > EMA(50) → LONG bias only · Close < EMA(50) → SHORT bias only |
| **4H** | RSI(14) > 50 confirms LONG · RSI < 50 confirms SHORT |
| **1H (entry)** | RSI(14) in pullback zone (long > 45, short < 55) |
| | MACD(12,26,9): line vs signal must agree |
| | Bullish/Bearish engulfing (any-size, body > 1.0× prev body) |
| | ATR(14) > rolling 50-bar mean (volatility regime) |
| | Volume > 1.2× SMA(20) (real participation) |

### Exit

- **Stop loss** — pattern-based (low/high of entry candle ± 0.1% buffer, capped at 2.5% from entry).
  Placed as `STOP_MARKET` on Binance immediately after entry → fires intrabar even if bot is offline.
- **Partial TP** — at +3R favorable, lock 30% of position, leave 70% running on original SL.
- **Opposite signal** — when reverse setup fires, exit position. **Do NOT open opposite** (V4 fix
  prevents giving back profits to bounces).
- **No fixed TP** — let runners run.

### Risk management

- **Same-direction cooldown after SL** — 36 hours (V5 sweep optimum)
- **Drawdown circuit breaker** — halt all trading 7 days after −25% peak-to-trough drawdown
- **Position sizing** — risk 1% of equity per trade (sized off SL distance × leverage)
- **Leverage** — 2× (configurable in `config/{env}.json`)

---

## Files

```
bot.py            — V5 live bot (one tick per 1H bar close)
core.py           — Strategy V5 logic (signals + condition state for dashboard)
strategy_v5.py    — Standalone V5 backtest (regenerates report HTML)
server.py         — HTTP server (dashboard + API + auth for /settings)
dashboard.html    — Live UI (BTC chart with EMA50, setup status, position panel)
settings.html     — Password-protected config (API keys, email, passwords)
config/           — testnet.json, production.json
scripts/          — start.sh, stop.sh, status.sh, self_heal.sh, gcp_*.sh
data/             — runtime state per env (gitignored except cache/)
```

---

## Deploy

### Update workflow (local → GCP via scp)
```bash
gcloud compute scp bot.py core.py dashboard.html server.py strategy_v5.py \
  btc-bot-eu:~/BTC-Flip-Bot/ --zone=europe-west1-b
gcloud compute ssh btc-bot-eu --zone=europe-west1-b --command='sudo systemctl restart btc-bot-server'
```

The systemd timer triggers the bot every hour on the hour — no restart needed unless logic changed.

### Local testing
```bash
python3 bot.py --env testnet --dry      # dry run, no orders placed
python3 strategy_v5.py                  # regenerate backtest + report_v5.html
```

---

## Dashboard

- URL: `http://VM-IP:8888/dashboard.html?env=testnet`
- Public (no password) — read-only
- `/settings.html` is password-protected (set on first visit)
- Live BTC/USDT 1H candlestick chart with EMA(50) overlay (Binance WebSocket)
- Setup Status panel: every entry condition shown with met/pending state
- Position panel (when in trade): entry, SL distance, partial TP progress, live PnL
- Equity curve + recent trades table

## Email alerts

Bot sends Gmail SMTP alerts on:
- Trade open
- Trade close (SL / EXIT-OPP)
- Partial TP fired
- DD halt triggered

Configure in `.env`:
```
BOT_EMAIL=you@gmail.com
BOT_EMAIL_PASS=app-password   # Gmail App Password, not your real pw
BOT_EMAIL_TO=alerts@example.com
```

---

## Strategy validation summary

Every V5 parameter was sweep-tested:

| Parameter | Tested values | Optimum |
|---|---|---|
| Same-direction cooldown after SL | 0, 4, 8, 12, 16, 20, 24, **36**, 48, 72, 168h | **36h** |
| SL cap (% from entry) | 1.5%, 2.0%, **2.5%**, 3.5%, 5.0%, 7.5% | **2.5%** (pattern-based) |
| Partial TP trigger | 1.5R, 2R, 2.5R, **3R**, 4R, 5R, 6R, 8R | **3R** (lock 30%) — best WR + lowest DD |
| Engulfing body size multiplier | **1.0×**, 1.2×, 1.5× | **1.0×** (any-size engulfing) |
| Cooldown approach | time, fresh-signal reset, 4H RSI cycle | **time-based** |
| Flip-open after opposite signal | enabled / disabled | **disabled** (V4 fix) |
| Volume filter (vol > 1.2× SMA20) | on / off | **on** (off doubles trades but kills CAGR) |
| Leverage | 1×, **2×**, 3×, 4×, 5× | **2×** default (3× viable, see deploy) |

Post-mortem audit on the 5yr backtest:
- **94% of SL hits were correct** — price continued adverse direction within 24h
- Only 5.9% reversed within 24h (the SL was "wrong" in those rare cases)
- 56% of winners exited at right time, 44% could have held marginally longer

---

## License

Personal use. No warranty. Backtest results don't guarantee live performance — start on testnet.
