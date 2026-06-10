# BTC RSI Scalper

> Counter-trend RSI mean-reversion bot for Bybit BTCUSDT perpetual futures. 5-minute bars, 5× leverage, 2-leg DCA. Paper-tested 2019-2026.

**Project rename suggestion:** this repo is currently named `BTC-Flip-Bot` from a much earlier strategy. A more accurate name would be `btc-rsi-scalper` or `btc-counter-trend-bot`. Renaming the GitHub repo is optional and a separate step (Settings → Rename).

---

## What it does

Trades BTC perpetual futures on Bybit using RSI extremes as entry signals:
- **LONG** when 5-min RSI(9) ≤ 35 (oversold)
- **SHORT** when 5-min RSI(9) ≥ 65 (overbought)

It bets on mean reversion. When RSI hits an extreme, the bot opens a position and uses a 2-leg DCA pattern (a 2nd entry 0.5% adverse from the first) to average down on near-term continuations. Exits are TP, break-even-after-DCA, trailing stop, or a 6h/12h time-stop on stale positions.

This is a **counter-trend** strategy — it deliberately fades the move that triggered the RSI extreme. Filters block entries in conditions historically associated with runaway moves (high ATR, flat 15-min EMA gap).

---

## Honest backtest results

Sept 2019 → June 2026 (82 months / 6.8 years) of Binance BTCUSDT 5-min data. Linear sizing on a fixed $5K base. **Paper mode**: no fees included (live Bybit will subtract ~0.055% taker per side per leg).

### v2.1 (current proven baseline)

| Metric | Value |
|---|---|
| Total trades | 19,869 |
| Win rate | 71.9% (excluding BE-DCA $0 exits) |
| Net profit | +$726,366 |
| Max drawdown | 1.29% |
| Worst single trade | -$338 |
| Months profitable | 82 / 82 (100%) |
| TP for L1-only | 0.5% |
| TP after DCA (L2) | 0.25% |
| Time-stop | 6 hours |

### v2.2 (optimized, deployed alongside)

| Metric | Value |
|---|---|
| Total trades | 19,140 |
| Win rate | 72.1% |
| Net profit | +$884,471 |
| Max drawdown | 0.64% |
| Worst single trade | -$130 |
| Months profitable | 82 / 82 (100%) |
| TP for L1-only | 0.5% |
| TP after DCA (L2) | 1.00% |
| Time-stop | 12 hours |

**v2.2 vs v2.1:** +22% more profit, -50% lower drawdown, same trade flow. The difference: v2.2 holds positions longer for bigger TPs, with a wider time-stop as the safety net.

### Validation

- **Independent reimplementation** (fresh Python, written from spec) on 2019 Sept-Dec: 686 trades / 69.8% WR / $24,832 profit. Matches this codebase within 5%.
- **Jesse framework** cross-check on 30-day window: matched within margin.
- **Out-of-sample**: train 2019-2022, test 2023-2026. Test/train profit ratio 0.59× — natural regime drift, not curve-fitting.
- **Walk-forward**: same parameters profitable in every year independently.
- **Anti-lookahead audit**: next-bar fills, blocked same-bar DCA+exit, indicators use closed-bar timestamps.

### Realistic live expectations

Backtest is paper mode. Real Bybit applies:
- Taker fee 0.055% per side (~$26 per L2 trade at $5K base)
- Funding rate (~$30-150/day per $237K notional, not modeled)
- Cron timing misses (~10-20% of signals)
- Slippage at scale (modeled 0.02%, live may exceed)

Expected live degradation: 30-50% off backtest. v2.2's 6.8-year backtest avg of ~$10,800/month on $5K becomes a realistic **$5,000-7,000/month** range — still 5-7× the typical 30%/month investment target. The 30-day paper observation phase is the honest anchor.

---

## Architecture

```
bot_rsiscalp_v3.py     ← the bot (single Python file, ~800 lines)
core_rsiscalp.py       ← shared utilities (signals, sizing, SL math)
data_bybit.py          ← Bybit REST/WS data fetcher
```

Both v2.1 and v2.2 run the **same bot code**. The only difference is launcher env vars:

```bash
# v2.1 launcher (defaults)
RSISCALP_LEVERAGE=5.0
RSISCALP_RSI_OVERSOLD=35
RSISCALP_RSI_OVERBOUGHT=65
RSISCALP_TP_DCA=0.0025      # 0.25%
RSISCALP_TIME_SL_BARS=72    # 6h

# v2.2 launcher
RSISCALP_TP_DCA=0.01        # 1.00%
RSISCALP_TIME_SL_BARS=144   # 12h
```

Cron runs each launcher every minute. The bot reads state from `data/paper_<bot>/state.json`, checks for signals, and either opens, manages, or closes a position.

---

## Quick start (local paper test)

```bash
git clone https://github.com/jagadeeshkmanne/BTC-Flip-Bot.git
cd BTC-Flip-Bot
pip3 install numpy pandas requests reportlab

# Run v2.1 once (single tick)
RSISCALP_DATA_DIR=paper_local \
  python3 strategies/day/bot_rsiscalp_v3.py
```

The bot fetches latest Bybit data, evaluates the signal, and updates `data/paper_local/state.json`. Run it every minute (cron, or `watch -n 60`) to operate live.

---

## GCP deployment

### Prerequisites

1. Install gcloud CLI: https://cloud.google.com/sdk/docs/install
2. Authenticate: `gcloud auth login`
3. Create project + enable billing: `gcloud projects create btc-bot-<yourname>`
4. Set project: `gcloud config set project btc-bot-<yourname>`

### Step 1: Create VM

```bash
./scripts/gcp_create_vm.sh
```

Creates an `e2-micro` VM (free tier) in `europe-west1-b` with Debian. Stays free under GCP's always-free quota (~$0/month).

### Step 2: Open firewall

```bash
./scripts/gcp_firewall.sh
```

Opens port 8888 for the dashboard.

### Step 3: Upload code

```bash
gcloud compute scp --recurse . btc-bot-eu:~/BTC-Flip-Bot \
  --zone=europe-west1-b --tunnel-through-iap
```

### Step 4: Install dependencies on VM

```bash
gcloud compute ssh btc-bot-eu --zone=europe-west1-b --tunnel-through-iap
# Then on VM:
cd ~/BTC-Flip-Bot
./scripts/gcp_install.sh
```

### Step 5: Schedule cron

```bash
# On VM:
crontab -e
```

Add the active bots:

```cron
# v2.1 — conservative baseline (5× lev, TP_L2 0.25%, time-SL 6h)
* * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_trend_v3.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_trend_v3/cron.log 2>&1

# v2.2 — optimized L2 exits (5× lev, TP_L2 1.00%, time-SL 12h)
* * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_trend_v22.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_trend_v22/cron.log 2>&1

# Self-heal — restart server if it crashes
*/10 * * * * /home/jags/BTC-Flip-Bot/scripts/self_heal.sh
```

### Step 6: Start dashboard server

```bash
# On VM:
nohup python3 server.py > server.log 2>&1 &
```

Open: `http://<VM-IP>:8888/bots/v2.1`

---

## Configuration

All bot parameters are env-var driven. Common overrides:

| Var | Default | Description |
|---|---|---|
| `RSISCALP_LEVERAGE` | 5.0 | Leverage multiplier |
| `RSISCALP_RSI_OVERSOLD` | 35 | RSI level for LONG entry |
| `RSISCALP_RSI_OVERBOUGHT` | 65 | RSI level for SHORT entry |
| `RSISCALP_V2_GAP_MIN` | 0.002 | Min 15m EMA gap (0.20%) |
| `RSISCALP_ATR_MAX_PCT` | 0.80 | Max ATR as % of price |
| `RSISCALP_BE_WAIT_BARS` | 6 | Bars to wait before BE-DCA arms |
| `RSISCALP_TP_SINGLE` | 0.005 | L1-only TP (0.5%) |
| `RSISCALP_TP_DCA` | 0.0025 | Post-DCA TP (0.25% v2.1, 1.0% v2.2) |
| `RSISCALP_TIME_SL_BARS` | 72 | Time-stop bars (72=6h v2.1, 144=12h v2.2) |
| `RSISCALP_SL_FROM_WORST` | 0.006 | L1-only hard SL (0.6% from worst) |
| `RSISCALP_COUNTER_TREND` | 1 | 1 = counter-trend, 0 = with-trend |
| `RSISCALP_DAILY_MAX_LOSS_PCT` | 0.04 | Daily realized loss cap (4% of balance) |
| `RSISCALP_DAILY_MAX_LOSS` | 0 | Fixed-dollar daily cap (legacy, set PCT=0 to use) |
| `RSISCALP_SMART_TIME_SL` | 1 | Time-stop fires only when in loss |

---

## Risk model

- **L1-only state**: hard stop at 0.6% from L1 entry (-$71 max loss on $5K base).
- **After L2 fires**: hard stop deactivated. BE-DCA at avg = $0 gross loss on recovery. Time-stop forces exit at 6h (v2.1) or 12h (v2.2) if still losing.
- **L2 trail stop**: when peak goes ≥ 0.05% above avg, trail SL behind peak with 0.025% buffer. Locks in partial profit.
- **Daily loss cap**: 4% of balance, scales with capital ($20/day at $500, $200/day at $5K, $2K/day at $50K). Pauses new entries; doesn't close existing.
- **Single position per bot**: never more than one open position at a time.
- **15-min cooldown** after L1 hard SL or TIME_SL realizations. Skipped for BE-DCA ($0 isn't a real loss).

---

## Dashboard

Modern React dashboard at `/bots/v2.1` or `/bots/v2.2`. Shows:
- Live position state, entry/TP/SL markers on price scale
- Trade log with W/L/N classification (BE-DCA tracked as neutral)
- Stats: balance, drawdown, win rate, trade count
- All times displayed in IST (Asia/Kolkata) with AM/PM

Legacy HTML dashboard at root `/dashboard.html` — kept for compatibility but not actively maintained.

---

## Files

```
strategies/day/
  bot_rsiscalp_v3.py        ← main bot (shared by v2.1 + v2.2)
  core_rsiscalp.py          ← signals, sizing, SL math
  data_bybit.py             ← Bybit market data

scripts/
  run_paper_rsiscalp_trend_v3.sh    ← v2.1 launcher
  run_paper_rsiscalp_trend_v22.sh   ← v2.2 launcher
  gcp_create_vm.sh                  ← GCP VM provisioning
  gcp_firewall.sh                   ← Open dashboard port
  gcp_install.sh                    ← VM setup (run after upload)
  self_heal.sh                      ← Server restart on crash

backtest/
  sweep_optimize.py         ← parameter sweep harness
  test_loose_filters.py     ← filter relaxation tests
  (various)                 ← historical backtest scripts

dashboard-react/
  src/                      ← Preact + Tailwind dashboard
  package.json              ← npm run build → static/bots/

data/
  paper_rsiscalp_trend_v3/  ← v2.1 state + trade log
  paper_rsiscalp_trend_v22/ ← v2.2 state + trade log

server.py                   ← Dashboard backend (port 8888)
dashboard.html              ← Legacy HTML dashboard
```

---

## Capital flexibility

Strategy works at any capital level. Position size auto-scales:

| Capital | L1+L2 per leg | Daily cap (4%) | Expected backtest profit | Realistic live (50%) |
|---|---|---|---|---|
| $500 | $1,187 | $20/day | ~$1,000/mo | ~$500/mo |
| $1,000 | $2,375 | $40/day | ~$2,000/mo | ~$1,000/mo |
| $2,000 | $4,750 | $80/day | ~$4,000/mo | ~$2,000/mo |
| $5,000 | $11,875 | $200/day | ~$10,800/mo | ~$5,400/mo |
| $10,000 | $23,750 | $400/day | ~$21,600/mo | ~$10,800/mo |
| $50,000 | $118,750 | $2,000/day | ~$108,000/mo | ~$54,000/mo |

**Note on smaller capital:** Bybit's per-trade fees (~$26 per L2 trade at $5K base, scales down with capital) take a bigger relative bite when balances are small. $500 is the practical floor where fees still leave a positive expected return.

---

## Disclaimer

This is research code shared for educational purposes. Cryptocurrency trading carries substantial risk. Backtest results are not guarantees of future performance. Real-world execution involves fees, slippage, exchange outages, and market regime changes not fully captured in historical data.

**Run paper mode first.** Observe at least 30 days of live paper performance before committing real capital. Compare actual results to backtest expectations and set your real-money sizing accordingly.

Trade with capital you can afford to lose entirely.

---

## License

MIT. No warranty.
