import clsx from 'clsx';
import { ArrowUp, ArrowDown } from 'lucide-react';
import { useTickerStore } from '@/hooks/useBtcStream';

const fmtUsd = (n: number, d = 2) => '$' + n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtVol = (n: number) => {
  if (n > 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n > 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n > 1e3) return (n / 1e3).toFixed(2) + 'K';
  return n.toFixed(0);
};

/**
 * Top market bar — Binance-style.
 * Symbol + huge live price + 24h change + 24h stats. Neutral chrome.
 */
export function MarketHeader() {
  const { price, change24h, change24hPct, high24h, low24h, volume24h, flash, connected } = useTickerStore();
  const isUp = change24hPct >= 0;
  const flashCls = flash === 'up' ? 'animate-flash-green' : flash === 'down' ? 'animate-flash-red' : '';
  const changeColor = isUp ? 'text-accent-green' : 'text-accent-red';

  return (
    <div class="card card-pad">
      {/* Top row: symbol + live price + 24h change */}
      <div class="flex items-center justify-between gap-3">
        <div class="flex flex-col leading-tight min-w-0">
          <span class="text-2xs md:text-xs text-text-muted font-medium truncate">Bybit · USDT-M</span>
          <span class="text-text-bright font-semibold text-sm md:text-base tracking-tight mt-0.5">BTC/USDT</span>
          <span class={clsx('text-2xs mt-0.5 flex items-center gap-1',
            connected ? 'text-text-dim' : 'text-accent-red')}>
            <span class={clsx('size-1.5 rounded-full',
              connected ? 'bg-accent-green animate-pulse-slow' : 'bg-accent-red')} />
            {connected ? 'Live' : 'Reconnecting…'}
          </span>
        </div>

        <div class="flex items-end gap-2 min-w-0 shrink">
          <div class={clsx(
            'text-lg sm:text-2xl md:text-3xl font-bold font-mono tabular-nums tracking-tight leading-none px-1.5 rounded',
            changeColor, flashCls
          )}>
            {price > 0 ? fmtUsd(price) : '—'}
          </div>
          <div class={clsx('flex flex-col text-2xs md:text-sm font-mono leading-tight shrink-0', changeColor)}>
            <span class="flex items-center gap-0.5 font-semibold">
              {isUp ? <ArrowUp size={11} /> : <ArrowDown size={11} />}
              {isUp ? '+' : ''}{change24hPct.toFixed(2)}%
            </span>
            <span class="text-text-dim hidden sm:inline">{isUp ? '+' : ''}{change24h.toFixed(0)}</span>
          </div>
        </div>
      </div>

      {/* 24h stats row — 3-col grid on mobile, inline on desktop */}
      <div class="grid grid-cols-3 gap-2 mt-3 md:mt-4 pt-3 border-t border-bg-border md:border-0 md:pt-0 md:flex md:gap-x-8">
        <Stat label="24h High" value={fmtUsd(high24h, 0)} />
        <Stat label="24h Low"  value={fmtUsd(low24h, 0)} />
        <Stat label="24h Vol" value={`${fmtVol(volume24h)} BTC`} />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div class="flex flex-col leading-tight min-w-0">
      <span class="text-2xs text-text-dim">{label}</span>
      <span class="font-mono text-xs md:text-sm text-text font-medium mt-0.5 truncate">{value}</span>
    </div>
  );
}
