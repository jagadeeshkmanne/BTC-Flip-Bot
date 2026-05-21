"""bybit_client.py — Bybit V5 REST client for USDT-perpetual (linear) futures.

LIVE order placement. Used by bot_divflip_bybit.py to trade a real Bybit
account — including a Copy Trading Master Trader account (copy-trade orders go
through the same /v5/order/create endpoint, category=linear).

V5 auth: HMAC-SHA256 over  timestamp + api_key + recv_window + payload
  - GET  payload = query string (must match the query string actually sent)
  - POST payload = the exact JSON body string
Headers: X-BAPI-API-KEY / X-BAPI-TIMESTAMP / X-BAPI-RECV-WINDOW / X-BAPI-SIGN

Docs: https://bybit-exchange.github.io/docs/v5/intro
"""
from __future__ import annotations
import hmac, hashlib, json, time, logging
from typing import Optional, Any
import requests

log = logging.getLogger("bybit_client")

# retCodes that mean "your request was a no-op but that's fine"
_OK_NOOP_CODES = {
    110043,  # leverage not modified
    34040,   # set-leverage: not modified
    110025,  # position mode not modified
}


class BybitError(Exception):
    """Raised when a signed request comes back with a non-zero retCode that
    is not a benign no-op. The bot treats this as 'do nothing this tick'."""


class BybitClient:
    """Thin V5 REST wrapper. One instance per bot tick.

    base_url:
      mainnet  https://api.bybit.com
      testnet  https://api-testnet.bybit.com
      demo     https://api-demo.bybit.com   (Bybit demo trading)
    """

    def __init__(self, key: str, secret: str, base_url: str, recv_window: int = 5000):
        self.key = key or ""
        self.secret = secret or ""
        self.base = base_url.rstrip("/")
        self.recv_window = str(recv_window)
        self.s = requests.Session()

    # ─── signing ───
    def _sign(self, timestamp: str, payload: str) -> str:
        origin = timestamp + self.key + self.recv_window + payload
        return hmac.new(self.secret.encode(), origin.encode(), hashlib.sha256).hexdigest()

    def _request(self, method: str, path: str, params: Optional[dict] = None,
                 signed: bool = False) -> Optional[Any]:
        """Returns the `result` object on success, None on transport failure,
        raises BybitError on a non-benign API error."""
        params = params or {}
        url = self.base + path
        for attempt in range(3):
            timestamp = str(int(time.time() * 1000))
            headers = {}
            try:
                if method == "GET":
                    query = "&".join(f"{k}={v}" for k, v in params.items())
                    if signed:
                        headers = {
                            "X-BAPI-API-KEY": self.key,
                            "X-BAPI-TIMESTAMP": timestamp,
                            "X-BAPI-RECV-WINDOW": self.recv_window,
                            "X-BAPI-SIGN": self._sign(timestamp, query),
                        }
                    full = url + ("?" + query if query else "")
                    r = self.s.get(full, headers=headers, timeout=10)
                else:  # POST
                    body = json.dumps(params, separators=(",", ":")) if params else "{}"
                    if signed:
                        headers = {
                            "X-BAPI-API-KEY": self.key,
                            "X-BAPI-TIMESTAMP": timestamp,
                            "X-BAPI-RECV-WINDOW": self.recv_window,
                            "X-BAPI-SIGN": self._sign(timestamp, body),
                            "Content-Type": "application/json",
                        }
                    r = self.s.post(url, data=body, headers=headers, timeout=10)

                if r.status_code != 200:
                    log.warning(f"  HTTP {r.status_code} {path}: {r.text[:200]}")
                    # 4xx client errors (bad auth/params) won't fix on retry.
                    if r.status_code in (400, 401, 403, 404):
                        return None
                    time.sleep(2)
                    continue

                resp = r.json()
                code = resp.get("retCode")
                if code == 0:
                    return resp.get("result")
                if code in _OK_NOOP_CODES:
                    log.info(f"  {path}: retCode {code} ({resp.get('retMsg')}) — treated as no-op OK")
                    return resp.get("result", {})
                # Real API error — surface it so the caller skips this tick.
                raise BybitError(f"{path} retCode {code}: {resp.get('retMsg')}")
            except requests.RequestException as e:
                log.warning(f"  request error {path}: {e}")
                time.sleep(2)
        return None

    # ─── public market data (no auth) ───
    def klines(self, symbol: str, interval: str, limit: int = 500) -> Optional[list]:
        """interval: '1','3','5','15','30','60','240','D','W'. Bybit returns
        newest-first — we reverse to oldest-first to match Binance/pandas use."""
        res = self._request("GET", "/v5/market/kline", {
            "category": "linear", "symbol": symbol,
            "interval": interval, "limit": min(limit, 1000),
        })
        if not res or "list" not in res:
            return None
        # each row: [startTime, open, high, low, close, volume, turnover]
        return list(reversed(res["list"]))

    def live_price(self, symbol: str) -> Optional[float]:
        res = self._request("GET", "/v5/market/tickers", {
            "category": "linear", "symbol": symbol})
        if not res or not res.get("list"):
            return None
        return float(res["list"][0]["lastPrice"])

    def instrument_info(self, symbol: str) -> Optional[dict]:
        """Returns {qty_step, min_qty, tick} for rounding orders/prices."""
        res = self._request("GET", "/v5/market/instruments-info", {
            "category": "linear", "symbol": symbol})
        if not res or not res.get("list"):
            return None
        it = res["list"][0]
        lot = it.get("lotSizeFilter", {})
        prc = it.get("priceFilter", {})
        return {
            "qty_step": float(lot.get("qtyStep", 0.001)),
            "min_qty": float(lot.get("minOrderQty", 0.001)),
            "tick": float(prc.get("tickSize", 0.1)),
            "copy_trading": it.get("copyTrading", "none"),
        }

    # ─── account / position (signed) ───
    def wallet_balance(self) -> Optional[float]:
        """USDT equity available for trading. Tries UNIFIED then CONTRACT
        account types (copy-trading master accounts can be either)."""
        for acct in ("UNIFIED", "CONTRACT"):
            try:
                res = self._request("GET", "/v5/account/wallet-balance",
                                    {"accountType": acct}, signed=True)
            except BybitError as e:
                log.info(f"  wallet-balance {acct}: {e}")
                continue
            if not res or not res.get("list"):
                continue
            row = res["list"][0]
            for c in row.get("coin", []):
                if c.get("coin") == "USDT":
                    val = c.get("equity") or c.get("walletBalance") or "0"
                    if val not in ("", None):
                        return float(val)
            if row.get("totalEquity"):
                return float(row["totalEquity"])
        return None

    def position(self, symbol: str) -> Optional[dict]:
        """Current net position. Returns dict with side LONG/SHORT/None,
        qty, avg_price, unrealised_pnl, leverage — or None on fetch failure."""
        try:
            res = self._request("GET", "/v5/position/list", {
                "category": "linear", "symbol": symbol}, signed=True)
        except BybitError as e:
            log.warning(f"  position fetch failed: {e}")
            return None
        if res is None or "list" not in res:
            return None
        for p in res["list"]:
            size = float(p.get("size", 0) or 0)
            if size > 0:
                side = "LONG" if p.get("side") == "Buy" else "SHORT"
                return {
                    "side": side,
                    "qty": size,
                    "avg_price": float(p.get("avgPrice", 0) or 0),
                    "unrealised_pnl": float(p.get("unrealisedPnl", 0) or 0),
                    "leverage": float(p.get("leverage", 0) or 0),
                    "position_idx": int(p.get("positionIdx", 0) or 0),
                }
        return {"side": None, "qty": 0.0, "avg_price": 0.0,
                "unrealised_pnl": 0.0, "leverage": 0.0, "position_idx": 0}

    def set_leverage(self, symbol: str, leverage: float) -> bool:
        lev = str(int(leverage))
        try:
            self._request("POST", "/v5/position/set-leverage", {
                "category": "linear", "symbol": symbol,
                "buyLeverage": lev, "sellLeverage": lev}, signed=True)
            return True
        except BybitError as e:
            log.warning(f"  set_leverage: {e}")
            return False

    def market_order(self, symbol: str, side: str, qty: str,
                     reduce_only: bool = False) -> Optional[dict]:
        """Place a market order. side = 'Buy'/'Sell'. qty is a base-coin
        string already rounded to qtyStep. positionIdx 0 = one-way mode.
        Works for Copy Trading master accounts (same endpoint)."""
        params = {
            "category": "linear", "symbol": symbol,
            "side": side, "orderType": "Market",
            "qty": qty, "positionIdx": 0, "timeInForce": "IOC",
        }
        if reduce_only:
            params["reduceOnly"] = True
        try:
            return self._request("POST", "/v5/order/create", params, signed=True)
        except BybitError as e:
            log.error(f"  market_order failed: {e}")
            return None

    def limit_order(self, symbol: str, side: str, qty: str, price: str,
                    reduce_only: bool = False) -> Optional[dict]:
        """Place a GTC limit order. Used for the DCA legs — they rest on the
        book and fill at the exact trigger price (no market-order slippage),
        replicating the paper bot's marked-at-trigger fills."""
        params = {
            "category": "linear", "symbol": symbol,
            "side": side, "orderType": "Limit",
            "qty": qty, "price": price,
            "positionIdx": 0, "timeInForce": "GTC",
        }
        if reduce_only:
            params["reduceOnly"] = True
        try:
            return self._request("POST", "/v5/order/create", params, signed=True)
        except BybitError as e:
            log.error(f"  limit_order failed: {e}")
            return None

    def open_orders(self, symbol: str) -> Optional[list]:
        """Open (un-filled, un-cancelled) orders → list of
        {order_id, price, qty, side}, or None on fetch failure."""
        try:
            res = self._request("GET", "/v5/order/realtime", {
                "category": "linear", "symbol": symbol}, signed=True)
        except BybitError as e:
            log.warning(f"  open_orders: {e}")
            return None
        if not res or "list" not in res:
            return None
        return [{"order_id": o.get("orderId"),
                 "price": float(o.get("price", 0) or 0),
                 "qty": float(o.get("qty", 0) or 0),
                 "side": o.get("side")} for o in res["list"]]

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        try:
            self._request("POST", "/v5/order/cancel", {
                "category": "linear", "symbol": symbol,
                "orderId": order_id}, signed=True)
            return True
        except BybitError as e:
            log.info(f"  cancel_order {order_id}: {e}")
            return False

    def set_trading_stop(self, symbol: str, take_profit: Optional[str] = None,
                         stop_loss: Optional[str] = None) -> bool:
        """Set the position's server-side SL/TP (tpslMode=Full → closes the
        whole position when hit). Pass price strings already rounded to tick.
        This is what makes between-cron-tick spikes still exit at the right
        level — identical SL/BE/TP levels the bot computes each tick are
        pushed here so Bybit enforces them server-side."""
        # Trigger on LAST price (not the default MarkPrice) so SL/TP/BE fire on
        # the same price basis the paper bot checks each tick (live last price).
        params = {"category": "linear", "symbol": symbol,
                  "positionIdx": 0, "tpslMode": "Full",
                  "tpTriggerBy": "LastPrice", "slTriggerBy": "LastPrice"}
        if take_profit is not None:
            params["takeProfit"] = take_profit
        if stop_loss is not None:
            params["stopLoss"] = stop_loss
        try:
            self._request("POST", "/v5/position/trading-stop", params, signed=True)
            return True
        except BybitError as e:
            # "not modified" / same-price errors are benign
            log.info(f"  set_trading_stop: {e}")
            return False

    def closed_pnl(self, symbol: str, start_ms: Optional[int] = None,
                   limit: int = 50) -> list:
        """Realized-PnL records, newest first. Used to log a trade after a
        server-side SL/TP fired between cron ticks."""
        params = {"category": "linear", "symbol": symbol, "limit": limit}
        if start_ms:
            params["startTime"] = start_ms
        try:
            res = self._request("GET", "/v5/position/closed-pnl", params, signed=True)
        except BybitError as e:
            log.warning(f"  closed_pnl: {e}")
            return []
        if not res or "list" not in res:
            return []
        return res["list"]
