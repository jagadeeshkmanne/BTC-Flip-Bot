# BTC Flip Bot

BTC MACD+RSI momentum trading bot for Binance Futures with multi-timeframe analysis. Runs on GCP free tier ($0/month).

**Strategy (v3):** MACD(12,26,9) crossover + RSI(30-70) filter on 15m candles + 4H trend alignment. 2x leverage, 5% hard stop loss, MACD signal flip exits. 95% capital deployment with compounding, 5% reserve for funding fees.

**Backtest (180 days):** +185.6% return | 9.7% max drawdown | 1.49 profit factor | 35.6% win rate

**Features:** Multi-timeframe (15m + 4H) trend filter, testnet + production environments (independent toggle), live TradingView dashboard with 4H trend indicator, email notifications, password-protected settings UI, auto-compounding.

---

## Project Structure

```
BTC-Flip-Bot/
├── bot.py              # Main trading bot (MACD+RSI + 4H trend filter)
├── server.py           # Dashboard HTTP server + API
├── dashboard.html      # Live TradingView chart dashboard
├── settings.html       # Settings UI (keys, toggles, email)
├── .env                # API keys & email (NEVER push to git)
├── .gitignore          # Protects secrets
├── config/
│   ├── testnet.json    # Testnet strategy config
│   └── production.json # Production strategy config
├── data/
│   ├── testnet/        # state.json, trades.log
│   └── production/     # state.json, trades.log
├── backtest_sl_compare.py    # Backtest: Fixed SL vs ATR SL
├── backtest_mtf.py           # Backtest: Single vs Multi-Timeframe
├── backtest_monthly.py       # Backtest: Monthly P&L breakdown
└── backtest_results.html     # Visual backtest comparison dashboard
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
# Then edit production.json to change base_url and api_key_env/api_secret_env
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
2. Click project dropdown → New Project → name: `btc-flip-bot` → Create
3. Go to https://console.cloud.google.com/billing → link billing to project

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
  --tags=http-server

gcloud compute firewall-rules create allow-8888 \
  --allow=tcp:8888 \
  --target-tags=http-server
```

### Step 3: Install Dependencies

```bash
gcloud compute ssh btc-bot-eu --zone=europe-west1-b -- "sudo apt-get update -qq && sudo apt-get install -y -qq python3-pip && pip3 install pandas requests numpy 2>/dev/null || pip3 install --break-system-packages pandas requests numpy"
```

### Step 4: Add Swap Space (prevents crashes)

```bash
gcloud compute ssh btc-bot-eu --zone=europe-west1-b -- "sudo fallocate -l 512M /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab"
```

### Step 5: Upload Code

```bash
cd ~/Desktop/BTC-Flip-Bot
gcloud compute ssh btc-bot-eu --zone=europe-west1-b -- "mkdir -p ~/BTC-Flip-Bot/config ~/BTC-Flip-Bot/data/testnet ~/BTC-Flip-Bot/data/production"
gcloud compute scp bot.py server.py dashboard.html settings.html .gitignore btc-bot-eu:~/BTC-Flip-Bot/ --zone=europe-west1-b
gcloud compute scp config/testnet.json config/production.json btc-bot-eu:~/BTC-Flip-Bot/config/ --zone=europe-west1-b
gcloud compute scp .env btc-bot-eu:~/BTC-Flip-Bot/.env --zone=europe-west1-b
echo "disabled" | gcloud compute ssh btc-bot-eu --zone=europe-west1-b -- "cat > ~/BTC-Flip-Bot/data/production/.disabled"
```

### Step 6: Setup Systemd Services

SSH into the VM and create the services:

```bash
gcloud compute ssh btc-bot-eu --zone=europe-west1-b
```

Then on the VM, create these files (see gcp_install.sh or copy from below):

Dashboard server (`/etc/systemd/system/btc-bot-server.service`):
```
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

Bot timers — create testnet and production `.service` and `.timer` files (every 15 min).

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable btc-bot-server btc-bot-testnet.timer btc-bot-production.timer
sudo systemctl start btc-bot-server btc-bot-testnet.timer btc-bot-production.timer
```

### Step 7: Get Your Dashboard URL

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
gcloud compute ssh btc-bot-eu --zone=europe-west1-b -- "sudo systemctl restart btc-bot-server"
```

---

## Push to GitHub

### First Time

```bash
cd ~/Desktop/BTC-Flip-Bot
git init
git add .
git commit -m "BTC Flip Bot v3 — MACD+RSI + 4H MTF filter"
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

**Safe to push:** bot.py, server.py, dashboard.html, settings.html, .gitignore, README.md, backtest scripts

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

## Delete Everything

### Delete GCP VM (stops billing)

```bash
gcloud compute instances delete btc-bot-eu --zone=europe-west1-b
gcloud compute firewall-rules delete allow-8888
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

Go to your repo → Settings → scroll to bottom → Delete this repository

---

## Useful Commands

### GCP / VM

```bash
# SSH into VM
gcloud compute ssh btc-bot-eu --zone=europe-west1-b

# Get VM IP
gcloud compute instances describe btc-bot-eu --zone=europe-west1-b \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'

# List all VMs (check for accidental extras)
gcloud compute instances list

# Upload updated code
cd ~/Desktop/BTC-Flip-Bot
gcloud compute scp bot.py server.py dashboard.html settings.html btc-bot-eu:~/BTC-Flip-Bot/ --zone=europe-west1-b
```

### On the VM

```bash
# Check bot services
sudo systemctl status btc-bot-testnet.timer
sudo systemctl status btc-bot-production.timer
sudo systemctl status btc-bot-server

# Restart dashboard after code update
sudo systemctl restart btc-bot-server

# View logs
journalctl -u btc-bot-testnet.service --no-pager -n 50
journalctl -u btc-bot-server --no-pager -n 30

# Manually trigger a bot run
cd ~/BTC-Flip-Bot && python3 bot.py --env testnet

# Check swap space
free -h
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
|-----------|---------|-------------|
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

- 4H MACD histogram > 0 AND RSI > 45 → BULLISH (only LONG entries allowed)
- 4H MACD histogram < 0 AND RSI < 55 → BEARISH (only SHORT entries allowed)
- Otherwise → NEUTRAL (both directions allowed)

Exits are NOT affected by the filter — positions always close on MACD flip or SL regardless of 4H trend.
