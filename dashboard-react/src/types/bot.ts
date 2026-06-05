// Strategy IDs match the Python bot's STRATEGY query param + data dir slugs.
export type StrategyId = 'rsiscalp_trend' | 'rsiscalp_trend_v2' | 'rsiscalp_trend_v3';

export interface BotMeta {
  id: StrategyId;
  short: string;        // "v1" / "v2" / "v3"
  label: string;        // "RSI Scalp +Trend v2"
  badge: string;        // "GAP firmness" / etc.
  accent: 'blue' | 'green' | 'purple';
  description: string;
}

export const BOTS: BotMeta[] = [
  {
    id: 'rsiscalp_trend',
    short: 'v1',
    label: 'RSI Scalp +Trend',
    badge: 'baseline',
    accent: 'blue',
    description: 'RSI(9) ≤30/≥70 + 15m EMA20/50 trend gate. 2-leg DCA @0.50%, adaptive TP 0.50%/0.25%, SL 1% from worst, 3× lev.',
  },
  {
    id: 'rsiscalp_trend_v2',
    short: 'v2',
    label: 'RSI Scalp +Trend v2',
    badge: 'GAP firmness',
    accent: 'green',
    description: 'v1 + GAP firmness filter — only enter when 15m |EMA20-EMA50|/EMA50 ≥ 0.25%. Skips knife-edge trends. Backtest: -75% → +44% OOS.',
  },
  {
    id: 'rsiscalp_trend_v3',
    short: 'v3',
    label: 'RSI Scalp +Trend v3',
    badge: 'risk-based',
    accent: 'purple',
    description: 'RISK-BASED sizing (Gemini-style). NO DCA. Single entry, position sized so SL hit = exactly 0.5% balance. Max loss/trade predictable.',
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
  pnl_usd?: number;
  pnl_pct?: number;
  entries?: number;
}
