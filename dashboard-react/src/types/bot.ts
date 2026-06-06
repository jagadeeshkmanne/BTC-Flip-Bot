// Strategy IDs match the Python bot's STRATEGY query param + data dir slugs.
export type StrategyId = 'rsiscalp_trend' | 'rsiscalp_trend_v2' | 'rsiscalp_trend_v3' | 'rsiscalp_trend_v4' | 'rsiscalp_trend_v5';

export interface BotMeta {
  id: StrategyId;
  short: string;        // "v1" / "v2" / "v3" / "v4" / "v5"
  label: string;
  badge: string;
  accent: 'blue' | 'green' | 'purple' | 'orange' | 'red';
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
    badge: 'anti-breakout',
    accent: 'purple',
    description: 'v2 + anti-breakout filter — skips SHORT if last 3 closes > 5m BB upper OR current bar vol > 2× SMA(20). Targets "don\'t fade a moving train". Same risk profile as v2 (DCA + $180 max loss).',
  },
  {
    id: 'rsiscalp_trend_v4',
    short: 'v4',
    label: 'RSI Scalp +Trend v4',
    badge: 'no-DCA · tight SL',
    accent: 'orange',
    description: 'v3 entries + NO DCA + tight SL (0.5% from entry). Same selective filters as v3, but loss-capped at ~$75/trade (vs v3 $180). Tests whether removing DCA-amplification beats DCA-rescue.',
  },
  {
    id: 'rsiscalp_trend_v5',
    short: 'v5',
    label: 'RSI Scalp +Trend v5',
    badge: 'simple + R:R',
    accent: 'red',
    description: 'v1 entries (NO GAP, NO anti-breakout) + NO DCA + tight SL + new fleet-wide ATR/1h filters. Simplest entries + bounded loss. Tests: does v1\'s aggressive style + good R:R beat heavy filtering?',
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
  // 2026-06-05: excursion stats — max favorable + max adverse during trade
  max_fav_pct?: number;
  max_adv_pct?: number;
}
