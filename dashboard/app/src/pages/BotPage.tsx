import { useBotStatus, useBotState } from '@/api/bots';
import { useBtcStream } from '@/hooks/useBtcStream';
import { MarketHeader } from '@/components/MarketHeader';
import { BotStatsStrip } from '@/components/BotStatsStrip';
import { PositionPanel } from '@/components/PositionPanel';
import { ConditionsPanel } from '@/components/ConditionsPanel';
import { DailyCloseCountdown } from '@/components/DailyCloseCountdown';
import { BacktestPanel } from '@/components/BacktestPanel';
import { PriceChart } from '@/components/PriceChart';
import { TradeLogTable } from '@/components/TradeLogTable';
import { STRATEGY_TO_BOT, type StrategyId } from '@/types/bot';

export function BotPage({ strategy }: { strategy: StrategyId }) {
  // Mount the BTC WebSocket stream (singleton — one connection across all bot pages)
  useBtcStream();

  const status = useBotStatus(strategy);
  const state = useBotState(strategy);
  const bot = STRATEGY_TO_BOT[strategy];
  // Normalize the bot's trade_log to the schema the table expects:
  // bot writes {net_usd, exit_time, entry, exit, qty, reason}; table wants
  // {pnl_usd, pnl_pct, entry_time, ...}. Map without touching the bot.
  const trades = (state.data?.trade_log ?? []).map((t: any) => {
    const pnl_usd = t.pnl_usd ?? t.net_usd ?? 0;
    const notional = (t.qty ?? t.qty_total ?? 0) * (t.entry ?? t.avg_entry ?? 0);
    return {
      ...t,
      pnl_usd,
      pnl_pct: t.pnl_pct ?? (notional ? (pnl_usd / notional) * 100 : 0),
      entry_time: t.entry_time ?? t.exit_time,   // bot logs only exit_time
    };
  });

  return (
    <div class="space-y-3 md:space-y-4 min-w-0">
      {/* Strategy label */}
      <div class="min-w-0">
        <h1 class="text-sm md:text-lg font-bold tracking-tight flex items-baseline gap-2 flex-wrap">
          <span class="truncate">{bot.label}</span>
          <span class={`text-2xs font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded shrink-0 ${
            bot.accent === 'green'  ? 'bg-accent-green/15 text-accent-green'  :
            bot.accent === 'purple' ? 'bg-accent-purple/15 text-accent-purple' :
            bot.accent === 'orange' ? 'bg-accent-orange/15 text-accent-orange' :
            bot.accent === 'red'    ? 'bg-accent-red/15 text-accent-red'       :
                                      'bg-accent-blue/15 text-accent-blue'
          }`}>{bot.badge}</span>
        </h1>
        <p class="text-2xs text-text-dim mt-1 leading-relaxed">{bot.description}</p>
      </div>

      {/* Market header — huge live BTC price + 24h stats */}
      <MarketHeader />

      {/* Bot performance strip — Balance / P&L breakdown / Win Rate */}
      <BotStatsStrip status={status.data} state={state.data} />

      {/* Position panel — full width when in-trade (price-scale viz), waiting view when flat */}
      <PositionPanel status={status.data} state={state.data} strategy={strategy} />

      {/* Chart — BTC candles */}
      <PriceChart />

      {/* Daily-close countdown — when the daily MACD updates / the short can fire (btcv2) */}
      {strategy === 'btcv2' && (
        <DailyCloseCountdown
          shortTrigger={(status.data as any)?.v2gates?.short_fires_below}
          longTrigger={(status.data as any)?.v2gates?.long_fires_above}
        />
      )}

      {/* Conditions checklist — rsiscalp panel (v2.x incl. v2.3 regime router) */}
      <ConditionsPanel status={status.data} strategy={strategy} />

      {/* Backtest results + month-by-month (btcv2 only) */}
      {strategy === 'btcv2' && <BacktestPanel backtest={(status.data as any)?.backtest} />}

      {/* Trade log */}
      <TradeLogTable trades={trades} />
    </div>
  );
}
