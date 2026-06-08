// Strategy IDs match the Python bot's STRATEGY query param + data dir slugs.
// Only 2 active bots after 2026-06-08 cleanup: v1 (was v11) + v2 (was v3).
export type StrategyId = 'rsiscalp_trend_v11' | 'rsiscalp_trend_v3';

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
    description: 'RSI 35/65 + 15m trend gate + GAP 0.15% + ATR 0.8% + DCA + BE-after-DCA (wait 3 bars) + smart 6h time-SL. Honest 5y backtest: 6,782 trades / 55% WR / $64K / 2.08% DD / PF 2.13.',
  },
  {
    id: 'rsiscalp_trend_v3',
    short: 'v2',
    label: 'v2 · Counter-Trend',
    badge: 'Counter-Trend',
    accent: 'purple',
    description: 'Same as v1 but BYPASSES the 15m trend gate — RSI extremes fire regardless of trend. GAP 0.20% + BE wait 6. Honest 5y backtest: 12,859 trades / 65% WR / $174K / 1.29% DD / PF 3.07.',
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
  pnl_usd?: number;      // NET P&L (price move - fees)
  pnl_pct?: number;      // NET % (net / starting balance)
  fee_usd?: number;      // round-trip fees in dollars
  entries?: number;
  // 2026-06-05: excursion stats — max favorable + max adverse during trade
  max_fav_pct?: number;
  max_adv_pct?: number;
}
