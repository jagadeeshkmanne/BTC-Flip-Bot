// Strategy IDs match the Python bot's STRATEGY query param + data dir slugs.
// 2026-06-10: 2 counter-trend 5× variants for clean A/B comparison.
// v2 (3× lev) removed — same strategy as v2.1 but lower leverage = less profit.
export type StrategyId = 'v2.1' | 'v2.2';

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
    id: 'v2.1',
    short: 'v2.1',
    label: 'v2.1 · Counter-Trend (5× lev)',
    badge: 'Counter-Trend 5×',
    accent: 'purple',
    description: '5× leverage + NO weekend boost. TP for L2 = 0.25%, smart time-SL = 6h. 6.8y backtest: 19,869 trades / 71.9% WR / $726K / 1.29% DD.',
  },
  {
    id: 'v2.2',
    short: 'v2.2',
    label: 'v2.2 · Optimized L2 exits (5× lev)',
    badge: 'Counter-Trend 5× v2.2',
    accent: 'orange',
    description: 'v2.1 + wider TP for L2 (1.00% vs 0.25%) + longer smart time-SL (12h vs 6h). 6.8y backtest: 19,140 trades / 72.1% WR / $884K / 0.64% DD. +$158K profit (+22%), DD halved (-50%).',
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
