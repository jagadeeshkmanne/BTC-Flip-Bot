// Strategy IDs match the Python bot's STRATEGY query param + data dir slugs.
export type StrategyId = 'rsiscalp_trend' | 'rsiscalp_trend_v2' | 'rsiscalp_trend_v4' | 'rsiscalp_trend_v5';

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
    label: 'v1 · Ultimate',
    badge: 'Ultimate',
    accent: 'blue',
    description: 'Best 6-mo backtest config (+103% return, -2.94% DD). RSI + 15m trend + GAP firmness + ATR + 1h filters + DCA + tight SL 0.6% + trend-flip exit + weekend 2× sizing + daily-loss circuit breaker $200 + blocked transition hours (5,6,11,12,13,20 UTC).',
  },
  {
    id: 'rsiscalp_trend_v2',
    short: 'v2',
    label: 'v2 · DCA',
    badge: 'DCA',
    accent: 'green',
    description: 'RSI + 15m trend + GAP firmness ≥ 0.25% + ATR + 1h filters + 2-leg DCA. Standard SL 1% from worst entry. Backtest: +52% over 6 months, $-268 worst trade.',
  },
  {
    id: 'rsiscalp_trend_v4',
    short: 'v4',
    label: 'v4 · Capped + AB',
    badge: 'Capped + AB',
    accent: 'orange',
    description: 'RSI + 15m trend + GAP firmness + anti-breakout filter (last 3 closes vs 5m BB + vol-spike skip) + NO DCA + tight SL 0.5% from entry. Loss-capped at ~$75/trade. Maximum selectivity with bounded loss.',
  },
  {
    id: 'rsiscalp_trend_v5',
    short: 'v5',
    label: 'v5 · Capped',
    badge: 'Capped',
    accent: 'red',
    description: 'RSI + 15m trend + GAP firmness + ATR + 1h filters + NO DCA + tight SL 0.5% from entry. Same entries as v2 but loss-capped at ~$75/trade. Tests bounded-loss without anti-breakout filter.',
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
