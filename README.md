# BTC Flip Bot

BTC MACD momentum trading bot for Binance Futures with multi-timeframe trend filtering. Runs on GCP free tier ($0/month).

**Strategy (v4 — Option B):** MACD(12,26,9) fresh crossover on 15m candles, gated by 4H RSI trend filter. 2x leverage, 5% hard stop loss, instant MACD signal flip exits. Holds positions aligned with 4H trend through counter-trend noise. 95% capital deployment with compounding.

**Realistic Backtest (5 years, with fees + slippage):** +$92,912 profit (fixed $5K sizing) | $1,523/month avg | 52.5% win rate | 3.20 profit factor | 13.8% max drawdown | 90% profitable months

**Features:** Multi-timeframe (15m + 4H) trend filter, testnet + production environments, live TradingView dashboard with trade markers, email notifications, password-protected settings UI, auto-compounding, self-healing health monitor.

---

## Strategy Logic

### Entry Conditions (all must be true)

1. **4H RSI trend filter** — RSI(14) on 4H candles determines allowed direction: RSI > 50 = bullish (longs only), RSI < 50 = bearish (shorts only)
2. **Fresh MACD crossover** — MACD(12,26,9) must cross on the current 15m candle (not a stale cross from earlier)
3. **RSI within range** — 15m RSI between 30-70
4. **Volume filter** — Current volume >= 0.8x of 20-period SMA
5. **No cooldown** — At least 6 bars since last stop-loss hit

### Exit Conditions

- **MACD signal flip (aligned):** If a fresh MACD crossover fires in the opposite direction AND the 4H trend agrees, the bot closes the current position and immediately opens the opposite direction (a "flip")
- **MACD signal flip (counter-trend / Option B):** If the opposite MACD signal fires but the 4H trend still supports the current position, the bot HOLDS — it treats the counter-trend signal as noise
- **Stop loss:** 5% hard stop loss always active
- **Max hold:** 72 hours (configurable)

### Why Option B?

Backtested over 5 years, Option B (hold through counter-trend noise) dramatically outperforms Option A (close on any blocked flip): 52.5% win rate and 3.20 profit factor vs 36.1% WR and 1.54 PF.

---

## Project Structure

```
BTC-Flip-Bot/
├── bot.py                    # Main trading bot (MACD + 4H RSI trend filter)
├── server.py                 # Dashboard HTTP server + API
├── dashboard.html            # Live TradingView chart with trade markers
├── settings.html             # Settings UI (keys, toggles, email)
├── .env                      # API keys & email (NEVER push to git)
├── .gitignore                # Protects secrets
├── README.md
├── config/
│   ├── testnet.json          # Testnet strategy config
│   └── production.json       # Production strategy config
├── data/
│   ├── testnet/              # state.json, bot.log
│   └── production/           # state.json, bot.log
├── scripts/
│   ├── self_heal.sh          # Self-healing health monitor (cron job)
│   ├── gcp_create_vm.sh      # GCP VM creation script
│   ├── gcp_install.sh        # GCP dependency installer
│   ├── gcp_firewall.sh       # Firewall rule setup
│   ├── setup_cron.sh         # Cron job installer
│   ├── start.sh              # Start all services
│   ├── stop.sh               # Stop all services
│   └── status.sh             # Check service status
├── backtest_realistic.py     # Backtest with fees + slippage (primary)
├── backtest_flip_vs_hold.py  # Option A vs Option B comparison
├── backtest_1h.py            # 1H vs 15m entry comparison
├── backtest_mtf.py           # Single vs multi-timeframe comparison
├── backtest_monthly.py       # Monthly P&L breakdown
├── backtest_sl_compare.py    # Fixed SL vs ATR SL comparison
└── backtest_results.html     # Visual backtest dashboard
```

---

## Current GCP Setup

- **VM:** btc-bot-eu (europe-west1-b, e2-micro, free tier)
- **IP:** 34.14.124.215
- **Dashboard:** http://34.14.124.215:8888
- **Settings:** http://34.14.124.215:8888/settings.html
- **Password:** Set via settings page (HTTP Basic Auth)
- **SSH:** `gcloud compute ssh btc-bot-eu --zone=europe-west1-b`
- **GitHub:** https://github.com/jagadeeshkmanne/BTC-Flip-Bot (private)

---

## Self-Healing Health Monitor

The bot includes `scripts/self_heal.sh` — a cron job that runs every 5 minutes and auto-restarts any unhealthy services. This is critical for the e2-micro which can occasionally freeze under memory pressure.

### What It Monitors

1. **Dashboard server** — checks `systemctl is-active btc-bot-server`, AND verifies HTTP 200 from `localhost:8888` (catches frozen processes)
2. **Testnet timer** — checks `systemctl is-active btc-bot-testnet.timer`
3. **Production timer** — checks `systemctl is-active btc-bot-production.timer`
4. **Bot execution recency** — if `bot.log` hasn't been updated in 25+ minutes (15m interval + buffer), restarts the timer
5. **Available memory** — if below 50MB, clears kernel caches (`echo 3 > /proc/sys/vm/drop_caches`)
6. **Swap space** — if no swap detected, creates and enables a 512MB swapfile

### Setup on GCP VM

```bash
# 1. Upload the script
gcloud compute scp scripts/self_heal.sh btc-bot-eu:~/BTC-Flip-Bot/scripts/ --zone=europe-west1-b

# 2. Make executable
gcloud compute ssh btc-bot-eu --zone=europe-west1-b -- "chmod +x ~/BTC-Flip-Bot/scripts/self_heal.sh"

# 3. Add to crontab (runs every 5 minutes)
gcloud compute ssh btc-bot-eu --zone=europe-west1-b -- '(crontab -l 2>/dev/null; echo "*/5 * * * * /home/$USER/BTC-Flip-Bot/scripts/self_heal.sh >> /home/$USER/BTC-Flip-Bot/data/heal.log 2>&1") | crontab -'

# 4. Verify
gcloud compute ssh btc-bot-eu --zone=europe-west1-b -- "crontab -l"
```

### Checking Self-Healer Logs

```bash
# On the VM:
tail -20 ~/BTC-Flip-Bot/data/heal.log

# Healthy output (logged once per hour to keep file small):
# 2026-04-12 15:00:01 [OK] All services healthy.

# Self-healing output:
# 2026-04-12 15:05:01 [HEAL] Dashboard server frozen (HTTP 000) — restarting...
# 2026-04-12 15:05:04 [HEAL] Self-healing actions completed.
```

---

## Fresh Setup (New Machine / New Laptop)

### Prerequisites
- macOS with Terminal
- Google account with GCP project
- Binance Futures Testnet API keys

### Step 1: Install Tools (Mac)

```bash
# Install Homebrew (skip if already have it)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Follow the "Next steps" it shows to add brew to PATH, then:
brew install google-cloud-sdk
```

### Step 2: Login to GCP

```bash
gcloud auth login
gcloud config set project btc-flip-bot
```

### Step 3: Get the Code

```bash
cd ~/Desktop
git clone https://github.com/jagadeeshkmanne/BTC-Flip-Bot.git
cd BTC-Flip-Bot
```

### Step 4: Restore Your Secrets

The `.env` and config files are NOT in git. You need to recreate them.

Create `.env`:
```bash
cat > .env << 'EOF'
TESTNET_API_KEY=your_testnet_key_here
TESTNET_API_SECRET=your_testnet_secret_here
PRODUCTION_API_KEY=
PRODUCTION_API_SECRET=
BOT_EMAIL=your-email@gmail.com
BOT_EMAIL_PASS=your_app_password
BOT_EMAIL_TO=your-email@gmail.com
DASHBOARD_PASS_HASH=your_hash_here
EOF
```

Create config files:
```bash
mkdir -p config
cat > config/testnet.json << 'EOF'
{
  "env": "testnet",
  "api_key_env": "TESTNET_API_KEY",
  "api_secret_env": "TESTNET_API_SECRET",
  "base_url": "https://demo-fapi.binance.com",
  "leverage": 2,
  "pair": "BTCUSDT",
  "interval": "15m",
  "candles_needed": 100,
  "macd_fast": 12,
  "macd_slow": 26,
  "macd_signal": 9,
  "rsi_period": 14,
  "rsi_long_min": 30,
  "rsi_long_max": 70,
  "rsi_short_min": 30,
  "rsi_short_max": 70,
  "fixed_tp_pct": 0,
  "trailing_tp_activate": 0,
  "trailing_tp_trail": 0,
  "tsl_activate": 0,
  "use_atr_stop": false,
  "atr_period": 14,
  "atr_stop_mult": 2.5,
  "dca_levels": [],
  "cooldown_bars": 6,
  "vol_filter_enabled": true,
  "vol_sma_period": 20,
  "vol_min_ratio": 0.8,
  "hard_sl_pct": 0.05,
  "max_hold_hours": 72,
  "capital_deploy_pct": 0.95,
  "total_capital_usdt": 5000,
  "mtf_enabled": true,
  "mtf_interval": "4h",
  "mtf_candles": 100
}
EOF

cp config/testnet.json config/production.json
# Then edit production.json: change base_url to https://fapi.binance.com
# and api_key_env/api_secret_env to PRODUCTION_API_KEY/PRODUCTION_API_SECRET
```

### Step 5: Create Data Directories

```bash
mkdir -p data/testnet data/production
# Disable production by default
echo "disabled" > data/production/.disabled
```

### Step 6: Test Locally

```bash
python3 bot.py --env testnet --test
```

---

## Deploy to GCP (First Time)

### Step 1: Create Project (Browser)
1. Go to https://console.cloud.google.com
2. Click project dropdown -> New Project -> name: `btc-flip-bot` -> Create
3. Go to https://console.cloud.google.com/billing -> link billing to project

### Step 2: Create VM (Terminal)

IMPORTANT: Use a non-US region. Binance blocks US IP addresses.

```bash
gcloud services enable compute.googleapis.com

gcloud compute instances create btc-bot-eu \
  --zone=europe-west1-b \
  --machine-type=e2-micro \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --tags=http-server,bot-server

gcloud compute firewall-rules create allow-bot-dashboard \
  --allow=tcp:8888 \
  --target-tags=bot-server
```

Note: The VM needs BOTH tags — `http-server` for general GCP access and `bot-server` to match the firewall rule. If the dashboard isn't loading, check tags with: `gcloud compute instances describe btc-bot-eu --zone=europe-west1-b --format='get(tags.items)'`

### Step 3: Install Dependencies

```bash
gcloud compute ssh btc-bot-eu --zone=europe-west1-b -- "sudo apt-get update -qq && sudo apt-get install -y -qq python3-pip && pip3 install pandas requests numpy 2>/dev/null || pip3 install --break-system-packages pandas requests numpy"
```

### Step 4: Add Swap Space (prevents crashes on e2-micro)

```bash
gcloud compute ssh btc-bot-eu --zone=europe-west1-b -- "sudo fallocate -l 512M /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab"
```

### Step 5: Upload Code

```bash
cd ~/Desktop/BTC-Flip-Bot

# Create directories on VM
gcloud compute ssh btc-bot-eu --zone=europe-west1-b -- "mkdir -p ~/BTC-Flip-Bot/config ~/BTC-Flip-Bot/data/testnet ~/BTC-Flip-Bot/data/production ~/BTC-Flip-Bot/scripts"

# Upload core files
gcloud compute scp bot.py server.py dashboard.html settings.html .gitignore btc-bot-eu:~/BTC-Flip-Bot/ --zone=europe-west1-b
gcloud compute scp config/testnet.json config/production.json btc-bot-eu:~/BTC-Flip-Bot/config/ --zone=europe-west1-b
gcloud compute scp .env btc-bot-eu:~/BTC-Flip-Bot/.env --zone=europe-west1-b
gcloud compute scp scripts/self_heal.sh btc-bot-eu:~/BTC-Flip-Bot/scripts/ --zone=europe-west1-b

# Disable production by default
echo "disabled" | gcloud compute ssh btc-bot-eu --zone=europe-west1-b -- "cat > ~/BTC-Flip-Bot/data/production/.disabled"
```

### Step 6: Setup Systemd Services

SSH into the VM:
```bash
gcloud compute ssh btc-bot-eu --zone=europe-west1-b
```

Create dashboard server (`/etc/systemd/system/btc-bot-server.service`):
```ini
[Unit]
Description=BTC Bot Dashboard Server
After=network.target
[Service]
Type=simple
User=jags
WorkingDirectory=/home/jags/BTC-Flip-Bot
ExecStart=/usr/bin/python3 /home/jags/BTC-Flip-Bot/server.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
```

Create testnet service (`/etc/systemd/system/btc-bot-testnet.service`):
```ini
[Unit]
Description=BTC Bot Testnet Run
[Service]
Type=oneshot
User=jags
WorkingDirectory=/home/jags/BTC-Flip-Bot
ExecStart=/usr/bin/python3 /home/jags/BTC-Flip-Bot/bot.py --env testnet
```

Create testnet timer (`/etc/systemd/system/btc-bot-testnet.timer`):
```ini
[Unit]
Description=BTC Bot Testnet Timer (every 15 min)
[Timer]
OnCalendar=*:0/15
Persistent=true
[Install]
WantedBy=timers.target
```

Create production service and timer the same way (replace `testnet` with `production`).

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable btc-bot-server btc-bot-testnet.timer btc-bot-production.timer
sudo systemctl start btc-bot-server btc-bot-testnet.timer btc-bot-production.timer
```

### Step 7: Setup Self-Healer

```bash
# Make executable
chmod +x ~/BTC-Flip-Bot/scripts/self_heal.sh

# Add to crontab
(crontab -l 2>/dev/null; echo "*/5 * * * * /home/$USER/BTC-Flip-Bot/scripts/self_heal.sh >> /home/$USER/BTC-Flip-Bot/data/heal.log 2>&1") | crontab -

# Verify
crontab -l
```

### Step 8: Get Your Dashboard URL

```bash
gcloud compute instances describe btc-bot-eu --zone=europe-west1-b \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

Open: `http://YOUR_IP:8888`

---

## Update Bot Code on GCP

After making changes locally:

```bash
cd ~/Desktop/BTC-Flip-Bot
gcloud compute scp bot.py server.py dashboard.html settings.html btc-bot-eu:~/BTC-Flip-Bot/ --zone=europe-west1-b
gcloud compute scp config/testnet.json config/production.json btc-bot-eu:~/BTC-Flip-Bot/config/ --zone=europe-west1-b
gcloud compute scp scripts/self_heal.sh btc-bot-eu:~/BTC-Flip-Bot/scripts/ --zone=europe-west1-b
gcloud compute ssh btc-bot-eu --zone=europe-west1-b -- "sudo systemctl restart btc-bot-server"
```

---

## Push to GitHub

### First Time

```bash
cd ~/Desktop/BTC-Flip-Bot
git init
git add .
git commit -m "BTC Flip Bot v4 — Option B strategy with self-healer"
git remote add origin https://github.com/jagadeeshkmanne/BTC-Flip-Bot.git
git branch -M main
git push -u origin main
```

### After Changes

```bash
git add .
git commit -m "describe your changes"
git push
```

**Safe to push:** bot.py, server.py, dashboard.html, settings.html, .gitignore, README.md, backtest scripts, scripts/

**NEVER pushed (protected by .gitignore):** .env, config/testnet.json, config/production.json, data/

---

## Migrate to New Laptop

1. Clone from GitHub: `git clone https://github.com/jagadeeshkmanne/BTC-Flip-Bot.git`
2. Install tools: `brew install google-cloud-sdk`
3. Login: `gcloud auth login && gcloud config set project btc-flip-bot`
4. Recreate `.env` with your API keys (see Fresh Setup Step 4)
5. Recreate config files (see Fresh Setup Step 4)
6. Create data dirs: `mkdir -p data/testnet data/production`
7. Your GCP VM is still running — access dashboard at `http://34.14.124.215:8888`
8. To SSH into existing VM: `gcloud compute ssh btc-bot-eu --zone=europe-west1-b`

**Important:** Your VM keeps running even if you change laptops. The bot runs on GCP independently. You only need the local setup to make code changes and push updates.

---

## Backtest Results Summary

All backtests use 5 years of BTCUSDT data (2021-2026) with MACD(12,26,9) + 4H RSI trend filter.

### Realistic Backtest (with 0.04% fees + 0.03% slippage)

| Sizing Mode | Profit | Monthly Avg | Win Rate | Profit Factor | Max DD |
|---|---|---|---|---|---|
| Fixed $5K | $92,912 | $1,523/mo | 52.5% | 3.20 | 13.8% |
| Compound (cap $10K) | $151,321 | $2,481/mo | 52.5% | 3.20 | 14.4% |
| Compound (cap $25K) | $327,944 | $5,376/mo | 52.5% | 3.20 | 17.5% |
| Compound (cap $50K) | $556,890 | $9,129/mo | 52.5% | 3.20 | 21.2% |

90% of months are profitable with fixed sizing.

### Option A vs Option B Comparison

| Strategy | Win Rate | Profit Factor | Total Return |
|---|---|---|---|
| Option A (close on MTF-blocked flip) | 36.1% | 1.54 | +206% |
| **Option B (hold if aligned with 4H)** | **53.9%** | **3.68** | **+688%** |

### 15m vs 1H Entry Comparison

15m entry with 4H filter is the best combination: PF 1.55, DD 33.7%. All 1H configurations underperformed.

---

## Delete Everything

### Delete GCP VM (stops billing)

```bash
gcloud compute instances delete btc-bot-eu --zone=europe-west1-b
gcloud compute firewall-rules delete allow-bot-dashboard
```

### Delete GCP Project Entirely

```bash
gcloud projects delete btc-flip-bot
```

### Delete Local Files

```bash
rm -rf ~/Desktop/BTC-Flip-Bot
```

### Delete GitHub Repo

Go to your repo -> Settings -> scroll to bottom -> Delete this repository

---

## Useful Commands

### GCP / VM

```bash
# SSH into VM
gcloud compute ssh btc-bot-eu --zone=europe-west1-b

# Get VM IP
gcloud compute instances describe btc-bot-eu --zone=europe-west1-b \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'

# List all VMs
gcloud compute instances list

# Upload updated code
cd ~/Desktop/BTC-Flip-Bot
gcloud compute scp bot.py server.py dashboard.html settings.html btc-bot-eu:~/BTC-Flip-Bot/ --zone=europe-west1-b

# Check firewall tags (if dashboard won't load)
gcloud compute instances describe btc-bot-eu --zone=europe-west1-b --format='get(tags.items)'

# Add missing firewall tag
gcloud compute instances add-tags btc-bot-eu --zone=europe-west1-b --tags=bot-server
```

### On the VM

```bash
# Check all services at once
systemctl is-active btc-bot-server btc-bot-testnet.timer btc-bot-production.timer

# Detailed status
sudo systemctl status btc-bot-testnet.timer
sudo systemctl status btc-bot-production.timer
sudo systemctl status btc-bot-server

# Restart dashboard after code update
sudo systemctl restart btc-bot-server

# View bot logs
journalctl -u btc-bot-testnet.service --no-pager -n 50
journalctl -u btc-bot-server --no-pager -n 30

# View self-healer logs
tail -20 ~/BTC-Flip-Bot/data/heal.log

# Manually trigger a bot run
cd ~/BTC-Flip-Bot && python3 bot.py --env testnet

# Check memory and swap
free -h

# Check crontab (self-healer)
crontab -l
```

---

## GCP Billing & Usage

- **Dashboard:** https://console.cloud.google.com/billing
- **VM status:** https://console.cloud.google.com/compute/instances
- **Free tier:** e2-micro + 30GB disk = $0/month (one VM only)
- **Billing alerts:** https://console.cloud.google.com/billing/budgets (set a $1 alert)
- **IMPORTANT:** Binance blocks US IPs. Use europe-west1 or asia-southeast1 regions.

---

## Strategy Config Reference

Edit `config/testnet.json` or `config/production.json`:

| Parameter | Default | Description |
|---|---|---|
| interval | 15m | Candle timeframe |
| leverage | 2 | Futures leverage |
| hard_sl_pct | 0.05 | Stop loss (5% of P&L) |
| macd_fast/slow/signal | 12/26/9 | MACD parameters |
| rsi_period | 14 | RSI lookback |
| rsi_long_min/max | 30/70 | RSI filter for longs |
| rsi_short_min/max | 30/70 | RSI filter for shorts |
| cooldown_bars | 6 | Bars to wait after SL hit |
| vol_filter_enabled | true | Skip low-volume signals |
| vol_min_ratio | 0.8 | Min volume vs 20-SMA |
| total_capital_usdt | 5000 | Fallback capital (if account API fails) |
| capital_deploy_pct | 0.95 | % of balance to use (95%, 5% reserve for fees) |
| mtf_enabled | true | Enable 4H trend filter |
| mtf_interval | 4h | Higher timeframe for trend |
| mtf_candles | 100 | Candles to fetch for HTF |

### 4H Trend Filter Logic

- 4H RSI > 50 -> BULLISH (only LONG entries allowed)
- 4H RSI < 50 -> BEARISH (only SHORT entries allowed)

### Exit Behavior (Option B)

- **Aligned flip:** MACD flips AND 4H trend agrees with new direction -> close + open opposite (instant flip)
- **Counter-trend flip blocked:** MACD flips BUT 4H trend supports current position -> HOLD (ignore the noise)
- **Stop loss:** Always active at 5%, overrides everything
- **Max hold:** 72 hours timeout
