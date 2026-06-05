import { useMemo, useRef } from 'preact/hooks';
import { useVirtualizer } from '@tanstack/react-virtual';
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import clsx from 'clsx';
import type { TradeRecord } from '@/types/bot';

const fmtTime = (iso?: string) => {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
};
const dur = (a?: string, b?: string) => {
  if (!a || !b) return '—';
  const m = Math.round((new Date(b).getTime() - new Date(a).getTime()) / 60_000);
  return m < 60 ? `${m}m` : `${(m / 60).toFixed(1)}h`;
};

const ch = createColumnHelper<TradeRecord>();

export function TradeLogTable({ trades }: { trades: TradeRecord[] }) {
  // Newest first
  const data = useMemo(() => [...trades].reverse(), [trades]);

  const columns = useMemo(() => [
    ch.accessor('entry_time', { header: 'Time', cell: i => <span class="text-text-muted">{fmtTime(i.getValue())}</span> }),
    ch.accessor('side', { header: 'Side', cell: i => {
      const s = i.getValue();
      return <span class={clsx('pill', s === 'LONG' ? 'pill-green' : 'pill-red')}>{s}</span>;
    } }),
    ch.accessor('reason', { header: 'Exit', cell: i => {
      const r = i.getValue();
      return <span class={clsx('pill text-[10px]', r === 'TP' ? 'pill-green' : r === 'SL' ? 'pill-red' : 'pill-muted')}>{r || '—'}</span>;
    } }),
    ch.accessor('entries', { header: 'Legs', cell: i => i.getValue() ?? '—' }),
    ch.accessor(row => row.avg_entry ?? row.entry ?? row.first_entry, {
      id: 'entry',
      header: 'Entry $',
      cell: i => <span class="font-mono">{i.getValue()?.toFixed(2) ?? '—'}</span>,
    }),
    ch.accessor('exit', { header: 'Exit $', cell: i => <span class="font-mono">{i.getValue()?.toFixed(2) ?? '—'}</span> }),
    ch.accessor('pnl_pct', { header: 'P&L %', cell: i => {
      const v = i.getValue();
      if (v == null) return '—';
      return <span class={clsx('font-mono font-semibold', v >= 0 ? 'text-accent-green' : 'text-accent-red')}>
        {v >= 0 ? '+' : ''}{v.toFixed(2)}%
      </span>;
    } }),
    ch.accessor('pnl_usd', { header: '$ P&L', cell: i => {
      const v = i.getValue();
      if (v == null) return '—';
      return <span class={clsx('font-mono', v >= 0 ? 'text-accent-green' : 'text-accent-red')}>
        {v >= 0 ? '+' : ''}${v.toFixed(2)}
      </span>;
    } }),
    ch.display({
      id: 'duration',
      header: 'Held',
      cell: ctx => <span class="text-text-muted text-xs">{dur(ctx.row.original.entry_time, ctx.row.original.exit_time)}</span>,
    }),
  ], []);

  const table = useReactTable({ data, columns, getCoreRowModel: getCoreRowModel() });

  // Virtualize rows — fast at 1000+ trades
  const parentRef = useRef<HTMLDivElement>(null);
  const rowVirtualizer = useVirtualizer({
    count: table.getRowModel().rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 40,
    overscan: 8,
  });

  if (!trades.length) {
    return <div class="card text-center text-text-muted text-sm py-8">No trades yet.</div>;
  }

  return (
    <div class="card p-0 overflow-hidden">
      <div class="px-4 py-3 border-b border-bg-border flex items-center justify-between">
        <span class="text-sm font-semibold uppercase tracking-wide text-text-muted">Trade History</span>
        <span class="text-xs text-text-dim">{trades.length} trades</span>
      </div>
      {/* Header */}
      <div class="grid grid-cols-9 gap-3 px-4 py-2 text-[10px] uppercase tracking-wider text-text-dim border-b border-bg-border bg-bg/50">
        {table.getHeaderGroups()[0].headers.map(h => (
          <div key={h.id} class={clsx(h.id === 'side' && 'col-span-1', 'truncate')}>
            {flexRender(h.column.columnDef.header, h.getContext())}
          </div>
        ))}
      </div>
      {/* Virtualized rows */}
      <div ref={parentRef} class="overflow-auto" style={{ maxHeight: 'min(60vh, 500px)' }}>
        <div style={{ height: rowVirtualizer.getTotalSize(), width: '100%', position: 'relative' }}>
          {rowVirtualizer.getVirtualItems().map(virtualRow => {
            const row = table.getRowModel().rows[virtualRow.index];
            return (
              <div
                key={row.id}
                class="grid grid-cols-9 gap-3 px-4 py-2 text-sm border-b border-bg-border/40 hover:bg-bg-hover transition-colors items-center"
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  height: virtualRow.size,
                  transform: `translateY(${virtualRow.start}px)`,
                }}
              >
                {row.getVisibleCells().map(cell => (
                  <div key={cell.id} class="truncate">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
