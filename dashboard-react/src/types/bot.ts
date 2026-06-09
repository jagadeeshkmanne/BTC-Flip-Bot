// Strategy IDs match the Python bot's STRATEGY query param + data dir slugs.
// 2026-06-10: 3 bots — v1 (with-trend), v2 (counter-trend 3×), v2.1 (counter-trend 5×).
export type StrategyId = 'rsiscalp_trend_v11' | 'rsiscalp_trend_v2' | 'rsiscalp_trend_v3';

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
    id: 'rsiscalp_trend_v11',
    short: 'v1',
    label: 'v1 · With-Trend',
    badge: 'With-Trend',
    accent: 'blue',
    description: 'RSI 35/65 + 15m trend gate + GAP 0.15% + ATR 0.8% + DCA + BE-after-DCA (wait 3 bars) + smart 6h time-SL. 3× leverage, weekend 2×.',
  },
  {
    id: 'rsiscalp_trend_v2',
    short: 'v2',
    label: 'v2 · Counter-Trend (3× lev)',
    badge: 'Counter-Trend',
    accent: 'green',
    description: 'Counter-trend RSI 35/65 + GAP 0.20% + BE wait 6 + ATR 0.8%. 3× leverage + weekend 2× boost. 13mo linear backtest (fee-free): 1,844 tr / 66.3% WR / +$36,264 / 13/13 months profitable.',
  },
  {
    id: 'rsiscalp_trend_v3',
    short: 'v2.1',
    label: 'v2.1 · Counter-Trend (5× lev)',
    badge: 'Counter-Trend 5×',
    accent: 'purple',
    description: 'Same as v2 but 5× leverage + NO weekend boost (consistent sizing). 13mo linear backtest (fee-free): 2,139 tr / 65.7% WR / +$69,064 / 13/13 months profitable. ~90% more profit than v2.',
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
