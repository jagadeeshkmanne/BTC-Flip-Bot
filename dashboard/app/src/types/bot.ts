// Strategy IDs match the Python bot's STRATEGY query param + data dir slugs.
// 2026-06-25: fleet narrowed to btcv2 ONLY. Retired trend_btc (redundant),
// allweather (-76% DD), and btcalts (ret/DD collapses to 0.80 ex-2021 — its
// record was almost entirely the one-off 2021 alt bull). btcv2 holds ret/DD
// ~2.9 every year incl. ex-2021, and is the robust, regime-aware engine.
export type StrategyId = 'btcv2';

export interface BotMeta {
  id: StrategyId;
  short: string;
  label: string;
  badge: string;
  accent: 'blue' | 'green' | 'purple' | 'orange' | 'red';
  description: string;
}

export const BOTS: BotMeta[] = [
  {
    id: 'btcv2',
    short: 'V2',
    label: 'BTC V2 · 4h',
    badge: 'V2 · 4h',
    accent: 'red',
    description: "BTC V2 (4h) — the bot. Macro-filtered MTF long BTC (4h+daily EMA50/200 AND close>9mo SMA) with conviction leverage (1×→2.5× by ADX+EMA-gap) + pyramid@2R + lock-33%@6R + parabolic de-risk; shorts ETH on BTC's bear signal (down>10% from 35d high AND daily MACD<sig), bear-depth sized 0.5/1.0/1.0 (config C). Full 2017–2026, all 3 bears green: CAGR ~171%, −35% DD, ret/DD 4.90. Robust ex-2021 (ret/DD ~2.9). [PAPER]",
  },
];

export const STRATEGY_TO_BOT: Record<StrategyId, BotMeta> =
  Object.fromEntries(BOTS.map(b => [b.id, b])) as Record<StrategyId, BotMeta>;

// Server status.json shape (only fields we render).
export interface BotStatus {
  pair: string;
  price: number;
  live_price: number;
  balance: number;
  peak_equity: number;
  drawdown_pct: number;
  signal: 'LONG' | 'SHORT' | null;
  trend_15m: 'UP' | 'DOWN' | null;
  block_reason: string | null;
  // v2.3 regime router (null for v2.1/v2.2)
  regime?: {
    tf: string;
    adx: number | null;
    leg: 'trend' | 'range' | null;
    dir: 'up' | 'down' | null;
    trend_adx: number;
    range_adx: number;
    range_on: boolean;
    dual_tf: string | null;
  } | null;
  position: {
    side: 'LONG' | 'SHORT';
    first_entry: number;
    avg_entry: number;
    worst_entry: number;
    qty_total: number;
    filled: number;
    tp_px: number | null;
    sl_px: number | null;
    fav_pct: number;
    entry_time: string;
  } | null;
  indicators: {
    rsi: number;
    rsi_oversold: number;
    rsi_overbought: number;
    price: number;
    trend_gap_pct?: number;
    trend_gap_min_pct?: number;
  };
  stats: { total: number; wins: number; pnl: number };
  strategy: string;
  updated_at: string;
}

export interface BotState {
  balance: number;
  peak_equity?: number;
  stats: { total: number; wins: number; pnl: number };
  trade_log: TradeRecord[];
  closed_trades?: TradeRecord[];   // bot writes this field name
  pause_until?: string | null;      // ISO time when post-loss cooldown ends
  daily_loss?: number;              // negative if today's cumulative losses
  daily_loss_date?: string;
}

// ── All-weather basket (bot_allweather_4h.py status.json) ──
export interface BasketCoin {
  symbol: string;
  ok: boolean;
  price: number;
  live_price: number;
  signal: 'LONG' | 'SHORT';
  sub_equity: number;
  balance: number;
  position: {
    side: 'LONG' | 'SHORT';
    qty: number;
    avg_entry: number;
    entry_time: string;
    unrealized_usd: number;
  } | null;
  indicators: { ema_f: number; ema_g: number; funding_8h_pct: number; closed_bar: string };
  stats: { total: number; wins: number; losses: number; pnl: number };
}

export interface BasketStatus {
  basket: string[];
  balance: number;          // total basket equity
  peak_equity: number;
  drawdown_pct: number;
  coins: BasketCoin[];
  stats: { total: number; wins: number; losses: number; pnl: number };
  strategy: string;
  paper_mode: boolean;
  n_positions: number;
  updated_at: string;
}

export interface TradeRecord {
  entry_time: string;
  exit_time?: string;
  side: 'LONG' | 'SHORT';
  reason?: string;
  first_entry?: number;
  avg_entry?: number;
  entry?: number;
  exit?: number;
  qty?: number;
  qty_total?: number;    // bot's actual field name (preferred over qty)
  pnl_usd?: number;      // P&L $ (paper trading is fee-free)
  pnl_pct?: number;      // P&L % (pnl_usd / starting balance)
  entries?: number;
  // 2026-06-05: excursion stats — max favorable + max adverse during trade
  max_fav_pct?: number;
  max_adv_pct?: number;
}
