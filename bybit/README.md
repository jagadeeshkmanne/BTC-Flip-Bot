# Bybit — live trading credentials

This directory holds the `.env` file with Bybit API keys.

When the bot moves from paper to live Bybit trading, the order placement
logic will read credentials from `bybit/.env`:

```
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BYBIT_TESTNET=false       # true for testnet, false for mainnet
DASHBOARD_PASSWORD_HASH=...  # optional auth for dashboard
```

`.env` is gitignored — never commit it.

`server.py` looks for `bybit/.env` first, then falls back to `./.env`
at the repo root for backwards compatibility.
