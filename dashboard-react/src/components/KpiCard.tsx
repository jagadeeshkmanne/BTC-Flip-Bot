import clsx from 'clsx';

interface Props {
  label: string;
  value: string | number;
  hint?: string;
  delta?: number;        // signed; > 0 green, < 0 red. ALSO tints whole card if `tint` true.
  accent?: 'blue' | 'green' | 'red' | 'purple' | 'orange' | 'neutral';
  icon?: any;
  loading?: boolean;
  tint?: boolean;        // true = tint background + border by delta sign
  big?: boolean;         // bigger value (use for prominent KPIs like price/balance)
}

export function KpiCard({ label, value, hint, delta, accent, icon: Icon, loading, tint, big }: Props) {
  const isPos = delta != null && delta >= 0;
  const isNeg = delta != null && delta < 0;

  // Tint mode: whole card colored by delta sign
  const tintClass = tint && isPos
    ? 'border-accent-green/40 bg-gradient-to-br from-accent-green/8 to-bg-card'
    : tint && isNeg
    ? 'border-accent-red/40 bg-gradient-to-br from-accent-red/8 to-bg-card'
    : '';

  const accentColor = accent === 'green' ? 'text-accent-green'
                    : accent === 'red' ? 'text-accent-red'
                    : accent === 'purple' ? 'text-accent-purple'
                    : accent === 'orange' ? 'text-accent-orange'
                    : accent === 'neutral' ? 'text-text-muted'
                    : 'text-accent-blue';

  const valueColor = tint && isPos ? 'text-accent-green'
                   : tint && isNeg ? 'text-accent-red'
                   : '';

  return (
    <div class={clsx('card card-hover transition-colors', tintClass)}>
      <div class="flex items-start justify-between gap-2 mb-1">
        <span class="text-[10px] md:text-xs uppercase tracking-wider text-text-muted font-semibold">{label}</span>
        {Icon && <Icon size={14} class={clsx('opacity-60', accentColor)} />}
      </div>
      <div class={clsx(
        big ? 'text-2xl md:text-3xl' : 'text-xl md:text-2xl',
        'font-bold tracking-tight font-mono',
        valueColor,
        loading && 'opacity-40'
      )}>
        {value}
      </div>
      <div class="flex items-center gap-2 mt-1.5 text-xs">
        {delta != null && (
          <span class={clsx(
            'font-semibold tabular-nums',
            isPos ? 'text-accent-green' : 'text-accent-red'
          )}>
            {isPos ? '▲ +' : '▼ '}{delta.toFixed(2)}%
          </span>
        )}
        {hint && <span class="text-text-dim">{hint}</span>}
      </div>
    </div>
  );
}
