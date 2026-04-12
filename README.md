# BTC Flip Bot

BTC MACD+RSI momentum trading bot for Binance Futures. Runs on GCP free tier ($0/month).

**Strategy:** MACD(12,26,9) crossover + RSI(30-70) filter on 15-minute candles. 2x leverage, 5% hard stop loss, MACD signal flip exits. Backtested: +85% over 3 months, 19% max drawdown.

**Features:** Testnet + production environments (independent toggle), live TradingView dashboard, email notifications on trade close, web-based settings UI.

---

## Project Structure

```
BTC-Flip-Bot/
├── bot.py              # Main trading bot
├── server.py           # Dashboard HTTP server + API
├── dashboard.html      # Live TradingView chart dashboard
├── settings.html       # Settings UI (keys, toggles, email)
├── .env                # API keys & email (NEVER push to git)
├── .gitignore          # Protects secrets
├── config/
│   ├── testnet.json    # Testnet strategy config
│   └── production.json # Production strategy config
├── data/
│   ├── testnet/        # state.json, trades.log, bot.log
│   └── production/     # state.json, trades.log, bot.log
└── scripts/
    ├── gcp_create_vm.sh  # Create GCP VM
    ├── gcp_firewall.sh   # Open port 8888
    ├── gcp_install.sh    # Install services on VM
    ├── setup_cron.sh     # Setup cron (Mac local)
    ├── start.sh          # Start bot manually
    ├── stop.sh           # Stop bot manually
    └── status.sh         # Check bot status
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

If you pushed to GitHub:
```bash
cd ~/Desktop
git clone https://github.com/YOUR_USERNAME/BTC-Flip-Bot.git
cd BTC-Flip-Bot
```

If copying from another machine, just copy the `BTC-Flip-Bot` folder to `~/Desktop/`.

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
EOF
```

Or just start the server and use the Settings UI:
```bash
python3 server.py &
# Open http://localhost:8888/settings.html
# Enter your keys there
```

Create config files (copy from templates):
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
  "capital_deploy_pct": 1.00,
  "total_capital_usdt": 5000
}
EOF

cat > config/production.json << 'EOF'
{
  "env": "production",
  "api_key_env": "PRODUCTION_API_KEY",
  "api_secret_env": "PRODUCTION_API_SECRET",
  "base_url": "https://fapi.binance.com",
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
  "capital_deploy_pct": 1.00,
  "total_capital_usdt": 5000
}
EOF
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

```bash
gcloud services enable compute.googleapis.com

gcloud compute instances create btc-bot \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-standard \
  --tags=bot-server

gcloud compute firewall-rules create allow-bot-dashboard \
  --allow=tcp:8888 \
  --target-tags=bot-server \
  --source-ranges="0.0.0.0/0"
```

### Step 3: Upload & Install

```bash
# Upload code
gcloud compute scp --recurse ~/Desktop/BTC-Flip-Bot btc-bot:~ --zone=us-central1-a

# SSH into VM
gcloud compute ssh btc-bot --zone=us-central1-a

# On the VM:
sudo apt-get update -y && sudo apt-get install -y python3-pip python3-numpy
pip3 install pandas requests
cd ~/BTC-Flip-Bot && bash scripts/gcp_install.sh
```

### Step 4: Get Your Dashboard URL

```bash
# Run from your Mac (not the VM)
gcloud compute instances describe btc-bot --zone=us-central1-a \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

Open: `http://YOUR_IP:8888/settings.html`

---

## Update Bot Code on GCP

After making changes locally:

```bash
# Upload updated code
gcloud compute scp --recurse ~/Desktop/BTC-Flip-Bot btc-bot:~ --zone=us-central1-a

# SSH in and restart services
gcloud compute ssh btc-bot --zone=us-central1-a
sudo systemctl restart btc-bot-server
sudo systemctl daemon-reload
```

---

## Push to GitHub

### First Time

```bash
cd ~/Desktop/BTC-Flip-Bot
git init
git add .
git commit -m "BTC Flip Bot — MACD+RSI momentum strategy"

# Create repo on GitHub first, then:
git remote add origin https://github.com/YOUR_USERNAME/BTC-Flip-Bot.git
git branch -M main
git push -u origin main
```

### After Changes

```bash
git add .
git commit -m "describe your changes"
git push
```

**Safe to push:** bot.py, server.py, dashboard.html, settings.html, scripts/, .gitignore, README.md

**NEVER pushed (protected by .gitignore):** .env, config/testnet.json, config/production.json, data/

---

## Migrate to New Laptop

1. Clone from GitHub: `git clone https://github.com/YOUR_USERNAME/BTC-Flip-Bot.git`
2. Install tools: `brew install google-cloud-sdk`
3. Login: `gcloud auth login && gcloud config set project btc-flip-bot`
4. Recreate `.env` with your API keys (see Fresh Setup Step 4)
5. Recreate `config/testnet.json` and `config/production.json` (see Fresh Setup Step 4)
6. Create data dirs: `mkdir -p data/testnet data/production`
7. Your GCP VM is still running — access dashboard at `http://YOUR_VM_IP:8888`
8. To SSH into existing VM: `gcloud compute ssh btc-bot --zone=us-central1-a`

**Important:** Your VM keeps running even if you change laptops. The bot doesn't care about your local machine — it runs on GCP independently. You only need the local setup to make code changes and push updates.

---

## Delete Everything

### Delete GCP VM (stops billing)

```bash
gcloud compute instances delete btc-bot --zone=us-central1-a
gcloud compute firewall-rules delete allow-bot-dashboard
```

### Delete GCP Project Entirely

```bash
gcloud projects delete btc-flip-bot
```

Or go to: https://console.cloud.google.com/iam-admin/settings → select project → Shut down

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
gcloud compute ssh btc-bot --zone=us-central1-a

# Get VM IP
gcloud compute instances describe btc-bot --zone=us-central1-a \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'

# Stop VM (saves money if not on free tier)
gcloud compute instances stop btc-bot --zone=us-central1-a

# Start VM
gcloud compute instances start btc-bot --zone=us-central1-a

# Upload updated code
gcloud compute scp --recurse ~/Desktop/BTC-Flip-Bot btc-bot:~ --zone=us-central1-a
```

### On the VM

```bash
# Check bot services
sudo systemctl status btc-bot-testnet.timer
sudo systemctl status btc-bot-production.timer
sudo systemctl status btc-bot-server

# Restart dashboard after code update
sudo systemctl restart btc-bot-server

# View logs live
tail -f ~/BTC-Flip-Bot/data/testnet/bot.log

# Manually trigger a bot run
python3 ~/BTC-Flip-Bot/bot.py --env testnet

# Test connectivity
python3 ~/BTC-Flip-Bot/bot.py --env testnet --test
```

### Local Mac

```bash
# Run bot locally
cd ~/Desktop/BTC-Flip-Bot
python3 bot.py --env testnet

# Start dashboard locally
python3 server.py
# Open http://localhost:8888/settings.html

# Check status
bash scripts/status.sh testnet
```

---

## GCP Billing & Usage

- **Dashboard:** https://console.cloud.google.com/billing
- **VM status:** https://console.cloud.google.com/compute/instances?project=btc-flip-bot
- **Free tier:** e2-micro + 30GB disk in us-central1/us-east1/us-west1 = $0/month
- **Billing alerts:** https://console.cloud.google.com/billing/budgets (set a $1 alert just in case)

---

## Strategy Config Reference

Edit `config/testnet.json` or `config/production.json` to change strategy params:

| Parameter | Default | Description |
|-----------|---------|-------------|
| interval | 15m | Candle timeframe |
| leverage | 2 | Futures leverage |
| hard_sl_pct | 0.05 | Stop loss (5% of capital) |
| macd_fast/slow/signal | 12/26/9 | MACD parameters |
| rsi_period | 14 | RSI lookback |
| rsi_long_min/max | 30/70 | RSI filter for longs |
| rsi_short_min/max | 30/70 | RSI filter for shorts |
| cooldown_bars | 6 | Bars to wait after SL hit |
| vol_filter_enabled | true | Skip low-volume signals |
| vol_min_ratio | 0.8 | Min volume vs 20-SMA |
| total_capital_usdt | 5000 | Capital per environment |
| capital_deploy_pct | 1.00 | % of capital to use (1.0 = 100%) |
