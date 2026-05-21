# Bybit Divflip v1 — LIVE bot

Divergence-Flip **v1** strategy, ported to trade a **real Bybit USDT-perpetual
futures account** — including a **Copy Trading Master Trader** account (copy
orders auto-mirror to your followers; they go through the same V5 order
endpoint).

> ## ⚠️ Read this first
> Divflip ("config #5") is **documented as overfit**. The year-wise
> out-of-sample backtest (2021–2026, BTCUSDT 5m) returned **−100% — account
> wiped**. The +189% headline was in-sample on its own tuning window. See
> `../memory/divflip_tv_tuned_live.md` for the full evidence. This folder builds
> the live bot you asked for; deploying it trades **real money against that
> result**. To run it without placing orders, set `"trading_enabled": false` in
> `config/bybit_live.json` (monitor-only).

---

## Files

```
bybit/
  bot_divflip_bybit.py   — the live bot (systemd, every 1 min)
  bybit_client.py        — Bybit V5 signed REST client (orders, leverage, SL/TP)
  core.py                — indicators (copied verbatim from the paper bot)
  core_divflip.py        — divflip v1 strategy params + SL/BE/TP logic (copied verbatim)
  server.py              — standalone dashboard server (port 8889)
  dashboard.html         — the Bybit bot's own dashboard
  config/bybit_live.json — exchange / account / symbol settings
  .env.example           — API-key template (copy to .env)
  scripts/
    deploy_bybit.sh      — interactive GCP deploy (pick account/project/VM)
    gcp_install_bybit.sh — VM-side installer (connectivity check + systemd)
    vm_ip.sh             — print/lock the VM external IP (Bybit key whitelist)
    run_bybit.sh         — runner
  data/                  — state.json, status.json, bot.log (created at runtime)
```

## Strategy — SL / BE / TP are identical to the paper bot

Every strategy parameter lives in **`core_divflip.py`**, copied byte-for-byte
from the paper bot (`strategies/day/bot_divflip.py`). The live bot calls the
**same functions** — `sl_price_divflip`, `be_should_activate`, `per_level_qty`,
`dca_price`, `evaluate_signal_divflip`:

| Element | Behaviour (same as paper) |
|---|---|
| **Entry** | Fresh RSI divergence → LONG/SHORT, market order |
| **DCA** | 3 fixed-distance legs @ 0.35%, martingale 3:4:1.5 |
| **SL** | Composite — raw 1% from worst entry / BE floor / trailing peak ± 0.2%; tightest wins |
| **BE** | Arms at +0.55% favourable from **avg** entry, sticky |
| **TP** | 1% from **avg** entry, recomputed when DCA shifts the avg |

The only difference is **execution, not logic**: each tick the bot pushes the
computed SL + TP to Bybit's **server-side trading-stop**, so a wick between
1-minute cron ticks still exits at exactly the level the paper bot would use.
If price gaps past a level, the bot market-closes immediately that tick.

To change leverage / risk / DCA / SL / TP / BE, edit `core_divflip.py` — the
change applies to both this bot and the paper bot.

---

## Setup

### Step 1 — Bybit account + Master Trader

1. Have funds in the Bybit account you want the bot to trade.
2. Apply to become a Master Trader: <https://www.bybit.com/copyTrade/>
3. Set the **BTCUSDT** perpetual to **One-Way** position mode (not Hedge).
4. The bot calls `set-leverage` to 3× on every entry; if your account locks
   leverage, set BTCUSDT to **3×** manually so order sizing matches.

### Step 2 — Create the API key

Bybit → Profile → **API** → **Create New Key**:

- **Type:** System-generated API Key
- **Permissions:** Contract → **Orders & Positions** (mandatory for copy trading)
- **Account:** the Copy Trading Master Trader account
- **IP restriction:** recommended — whitelist your VM's external IP.
  Get it (and optionally lock it as a static IP) with `bash scripts/vm_ip.sh`

A read-only key silently fails on order placement — make sure Orders &
Positions is ticked.

### Step 3 — Configure

```bash
cd bybit
cp .env.example .env
# edit .env — paste BYBIT_API_KEY and BYBIT_API_SECRET
```

`config/bybit_live.json`:
- `base_url` — `https://api.bybit.com` (mainnet) · `https://api-demo.bybit.com`
  (demo trading, no real money — use this to verify mechanics first)
- `trading_enabled` — `true` places real orders · `false` = monitor-only
- `balance_override` — leave `0` to read equity from the wallet API. If a
  copy-trading account doesn't expose its balance via the API, set this to the
  USDT equity to size orders against.

### Step 4 — Test locally (no keys, no orders)

```bash
pip3 install -r requirements.txt
python3 bot_divflip_bybit.py --dry      # fetches Bybit data, places nothing
```

### Step 5 — Deploy to GCP

> **Region matters.** Bybit blocks US IP addresses (same as Binance). GCP's
> free tier is **US-only**, so a free-tier VM will **not** work for Bybit. Host
> in a Bybit-allowed region: `europe-west1` (Belgium), `europe-west3`
> (Frankfurt), `asia-northeast1` (Tokyo) or `asia-south1` (Mumbai). **Avoid**
> any `us-*`, `asia-southeast1` (Singapore), `asia-east2` (Hong Kong),
> `europe-west2` (London) and Canada regions — all Bybit-restricted. Simplest
> option: deploy onto your existing EU VM — it installs to a separate folder
> (`~/BTC-Flip-Bot-Bybit/`) and its own `bybit-divflip` timer, so it won't
> touch the Binance bots.

```bash
bash scripts/deploy_bybit.sh
```

Interactive — pick your **Google account → project → VM** (an existing one, or
create a new e2-micro in a Bybit-allowed region). It uploads the bot, opens the
dashboard firewall port, runs a connectivity preflight, starts the dashboard,
and starts the 1-minute trading timer **only if the preflight passes**. Re-run
it and pick differently to switch.

---

## Dashboard

The bot has its **own** dashboard — separate process, separate port (`8889`),
fully decoupled from the main project's `server.py` and dashboards.

```
http://<VM-IP>:8889/
```

It shows connectivity (API / keys / bot-alive dots), balance, the open position
(side, avg entry, the 3 DCA legs, SL / TP / breakeven, unrealised P&L), the live
signal + conditions, and the realised trade log — polling `data/status.json`
and `data/state.json` every 5 seconds. `gcp_install_bybit.sh` runs it as the
always-on `bybit-divflip-server` service.

## Connectivity check before the bot starts

`gcp_install_bybit.sh` runs a **preflight** (`bot_divflip_bybit.py --check`)
before enabling the trading timer — it tests public API reachability, key
validity, and account access. **The trading bot starts only if the preflight
passes.** If it fails (most often the API key's IP whitelist doesn't match the
VM's IP), the dashboard still runs so you can see the problem; fix it and re-run
the install script.

## Operating it

```bash
# logs
gcloud compute ssh <VM> --zone=<ZONE> --command='tail -f ~/BTC-Flip-Bot-Bybit/data/bot.log'

# VM external IP (for the Bybit key whitelist) — bash scripts/vm_ip.sh
# connectivity preflight  — python3 bot_divflip_bybit.py --check
# pause trading           — set "trading_enabled": false in config, redeploy
# stop the bot            — sudo systemctl disable --now bybit-divflip.timer
# stop the dashboard      — sudo systemctl disable --now bybit-divflip-server
# timer status            — sudo systemctl list-timers | grep bybit
```
