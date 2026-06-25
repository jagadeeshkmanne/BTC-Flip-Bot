// Strategy IDs match the Python bot's STRATEGY query param + data dir slugs.
// 2026-06-18: mean-reversion fleet (v2.1/v2.2/v2.3 rsiscalp) removed — no real
// edge. 2026-06-19: added the all-weather 4-coin basket (STRATEGY.md FINAL).
export type StrategyId = 'trend_btc' | 'allweather' | 'btcalts' | 'btcv2';

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
  {
    id: 'allweather',
    short: 'Basket',
    label: 'All-Weather · 4-coin',
    badge: 'Basket · 4h',
    accent: 'purple',
    description: 'All-weather 4-coin basket (BTC+ETH+BNB+SOL, equal weight, 1×) — 4h perp, long/short reverse. Per coin: EMA8>EMA200 → LONG, else SHORT; enter on the cross, reverse on the opposite cross (no stop, no TP). Best risk-adjusted of ~30 backtests (ret/DD 2.21, realistic ~42%/yr, −45% DD, in-sample). The STRATEGY.md FINAL. [PAPER]',
  },
  {
    id: 'btcalts',
    short: 'BTC-Alts',
    label: 'BTC-led Alts · 1h',
    badge: 'Alts · 1h',
    accent: 'orange',
    description: "BTC-led alt basket (1h, long/short, 1× vol-scaled) — the session's walk-forward winner. BTC slow trend (EMA32>EMA800) → LONG, else SHORT, applied to equal-weight ETH/BNB/SOL (high-beta to BTC). Exposure vol-scaled (de-levers in high vol). Top-3 beats wider baskets; shorts add the bear-year capture. WF ret/DD ~1.3–2.0, CAGR ~58–76%, −52% DD. [PAPER]",
  },
  {
    id: 'btcv2',
    short: 'V2',
    label: 'BTC V2 · 4h',
    badge: 'V2 · 4h',
    accent: 'red',
    description: "BTC V2 (4h, long/short, 1×) — the session's validated endpoint, derived from the Flip Bot V3 Pine, rebuilt honestly. Macro-filtered MTF long (4h+daily EMA50/200 AND close>9-month SMA) with pyramid@2R + parabolic de-risk (50% off when >120% above the 20-week SMA); bear-depth short (down>10% from 40d high AND daily MACD<sig, sized 0.25/0.5/1.0× by drawdown depth). Full 2017–2026, all 3 bears green: CAGR ~103%, −31% DD, ret/DD 3.34 (walk-forward 2.10). BTC-only — does not generalize to alts. [PAPER]",
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
