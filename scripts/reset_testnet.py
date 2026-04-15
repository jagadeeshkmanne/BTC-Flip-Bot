"""
Reset testnet state before deploying new bot.

Does:
  1. Cancels ALL open orders on BTCUSDT
  2. Closes ALL open positions on BTCUSDT (market order)
  3. Backs up local state files (state.json, trades.log, bot.log) with timestamp
  4. Resets state.json to clean defaults
  5. Reports current balance (you must reset balance manually via Binance demo web UI)

Usage:
  cd /Users/jags/Desktop/BTC-Flip-Bot
  python3 scripts/reset_testnet.py

Requires env vars: TESTNET_API_KEY, TESTNET_API_SECRET
"""
import os, sys, json, time, hmac, hashlib, shutil
from datetime import datetime
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTNET_DIR = os.path.join(ROOT, "data", "testnet")
BACKUP_DIR = os.path.join(ROOT, "data", "testnet", "backups")
CONFIG = os.path.join(ROOT, "config", "testnet.json")

with open(CONFIG) as f:
    cfg = json.load(f)
BASE = cfg["base_url"]
PAIR = cfg["pair"]

KEY = os.environ.get(cfg["api_key_env"])
SECRET = os.environ.get(cfg["api_secret_env"])
if not KEY or not SECRET:
    print(f"ERROR: Set {cfg['api_key_env']} and {cfg['api_secret_env']} env vars first")
    sys.exit(1)

S = requests.Session()
S.headers.update({"X-MBX-APIKEY": KEY})

def sign(params):
    q = "&".join(f"{k}={v}" for k,v in params.items())
    sig = hmac.new(SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    return params

def signed(method, path, params=None):
    params = params or {}
    params["timestamp"] = int(time.time()*1000)
    params["recvWindow"] = 5000
    params = sign(params)
    url = BASE + path
    r = S.request(method, url, params=params, timeout=10)
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}: {r.text}")
        return None
    return r.json()

print(f"Connecting to {BASE} ...")

# 1. Get current account info
acc = signed("GET", "/fapi/v2/account")
if acc is None:
    print("Failed to fetch account. Check API keys.")
    sys.exit(1)
balance = float(acc.get("totalWalletBalance", 0))
unrealized = float(acc.get("totalUnrealizedProfit", 0))
positions = [p for p in acc.get("positions", []) if float(p["positionAmt"]) != 0]

print(f"\n--- Current testnet state ---")
print(f"Wallet balance:        ${balance:,.2f}")
print(f"Unrealized PnL:        ${unrealized:,.2f}")
print(f"Open positions:        {len(positions)}")
for p in positions:
    print(f"  {p['symbol']}: {p['positionAmt']} @ entry {p['entryPrice']}, uPnL ${float(p['unRealizedProfit']):.2f}")

# 2. Cancel all open orders
print(f"\n--- Cancelling all open orders on {PAIR} ---")
orders = signed("GET", "/fapi/v1/openOrders", {"symbol": PAIR})
if orders:
    print(f"Found {len(orders)} open order(s). Cancelling...")
    res = signed("DELETE", "/fapi/v1/allOpenOrders", {"symbol": PAIR})
    print(f"  Result: {res}")
else:
    print("No open orders.")

# 3. Close any open position with market order
print(f"\n--- Closing open positions on {PAIR} ---")
btc_pos = next((p for p in positions if p["symbol"] == PAIR), None)
if btc_pos and float(btc_pos["positionAmt"]) != 0:
    amt = float(btc_pos["positionAmt"])
    side = "SELL" if amt > 0 else "BUY"
    qty = abs(amt)
    print(f"Closing {amt} BTCUSDT with {side} {qty}...")
    res = signed("POST", "/fapi/v1/order", {
        "symbol": PAIR,
        "side": side,
        "type": "MARKET",
        "quantity": qty,
        "reduceOnly": "true"
    })
    print(f"  Result: {res}")
else:
    print(f"No open position on {PAIR}.")

# 4. Back up + clear local state
print(f"\n--- Backing up + clearing local state ---")
os.makedirs(BACKUP_DIR, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
for fname in ["state.json", "trades.log", "bot.log"]:
    src = os.path.join(TESTNET_DIR, fname)
    if os.path.exists(src):
        dst = os.path.join(BACKUP_DIR, f"{fname}.{ts}")
        shutil.copy(src, dst)
        print(f"  Backed up {fname} → backups/{fname}.{ts}")

# Reset state.json to clean defaults
clean_state = {
    "position": None,
    "consecutive_losses": 0,
    "halt_until": None,
    "weekly_start_eq": None,
    "weekly_start_ts": None,
    "last_signal_bar": None,
    "trades_today": 0,
    "last_trade_date": None
}
with open(os.path.join(TESTNET_DIR, "state.json"), "w") as f:
    json.dump(clean_state, f, indent=2)
print(f"  state.json reset to defaults")

# Truncate logs
for fname in ["trades.log", "bot.log"]:
    p = os.path.join(TESTNET_DIR, fname)
    open(p, "w").close()
    print(f"  {fname} cleared")

# 5. Verify final state
print(f"\n--- Verifying ---")
time.sleep(2)
acc = signed("GET", "/fapi/v2/account")
new_bal = float(acc.get("totalWalletBalance", 0))
new_pos = [p for p in acc.get("positions", []) if float(p["positionAmt"]) != 0]
print(f"Wallet balance: ${new_bal:,.2f}")
print(f"Open positions: {len(new_pos)}")

print(f"\n{'='*60}")
print("RESET COMPLETE")
print('='*60)
print(f"Local state:        ✓ cleared (backup in data/testnet/backups/)")
print(f"Open orders:        ✓ cancelled")
print(f"Open positions:     ✓ closed")
print(f"")
print(f"⚠ Wallet balance is ${new_bal:,.2f}")
print(f"⚠ Binance demo futures does NOT allow programmatic balance reset.")
print(f"")
print(f"To reset balance to $5,000:")
print(f"  1. Open https://testnet.binancefuture.com/")
print(f"  2. Log in with your testnet account")
print(f"  3. Click 'Reset USDT Balance' (or 'Get Test Funds')")
print(f"  4. Confirm balance is now $5,000 (or higher)")
print(f"  5. Then start the new bot")
