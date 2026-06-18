// Strategy IDs match the Python bot's STRATEGY query param + data dir slugs.
// 2026-06-18: mean-reversion fleet (v2.1/v2.2/v2.3 rsiscalp) removed — no real
// edge. The validated dynamic-leverage 4h trend bot is the sole live bot.
export type StrategyId = 'trend_btc';

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
    id: 'trend_btc',
    short: 'Trend',
    label: 'Trend · 4h Dynamic-Lev',
    badge: 'Trend · 4h',
    accent: 'green',
    description: 'Dynamic-leverage 4h BTC perp trend, long/flat — no shorts, no fixed TP. LONG when EMA13>EMA20 AND close>EMA200; exit on flip (full ride). Leverage 2.5×↔5× by ADX+weekly conviction, vol-targeted, de-levered in high-vol/funding/daily-bear (avg ~1.1×). Backtest OOS ~50–65% CAGR / ~28% DD, zero liquidations in 6.7y. The session-winner config. [PAPER]',
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
