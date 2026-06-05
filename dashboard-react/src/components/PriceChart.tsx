import { useEffect, useMemo, useRef } from 'preact/hooks';
import { createChart, ColorType, LineStyle, CandlestickSeriesOptions, type IChartApi, type ISeriesApi } from 'lightweight-charts';
import { useKlines } from '@/api/bots';

// Bollinger Bands (n, k) — same formula as bot's pandas .rolling(n).std(ddof=0).
function bollinger(closes: number[], n = 20, k = 2.0) {
  const up: (number | null)[] = new Array(closes.length).fill(null);
  const mid: (number | null)[] = new Array(closes.length).fill(null);
  const lo: (number | null)[] = new Array(closes.length).fill(null);
  for (let i = n - 1; i < closes.length; i++) {
    let s = 0;
    for (let j = i - n + 1; j <= i; j++) s += closes[j];
    const m = s / n;
    let v = 0;
    for (let j = i - n + 1; j <= i; j++) v += (closes[j] - m) ** 2;
    const sd = Math.sqrt(v / n);
    up[i] = m + k * sd;
    mid[i] = m;
    lo[i] = m - k * sd;
  }
  return { up, mid, lo };
}

export function PriceChart() {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  const { data: klines5m } = useKlines('5m', 288);
  const { data: klines15m } = useKlines('15m', 192);

  // Init chart once
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#9ca3af',
        fontFamily: 'Inter, system-ui, sans-serif',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1f2937', style: LineStyle.Dotted },
        horzLines: { color: '#1f2937', style: LineStyle.Dotted },
      },
      timeScale: {
        borderColor: '#1f2937',
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: { borderColor: '#1f2937' },
      crosshair: {
        vertLine: { color: '#3b82f6', width: 1, labelBackgroundColor: '#3b82f6' },
        horzLine: { color: '#3b82f6', width: 1, labelBackgroundColor: '#3b82f6' },
      },
    });
    chartRef.current = chart;
    const ro = new ResizeObserver(entries => {
      const e = entries[0];
      chart.applyOptions({ width: e.contentRect.width, height: e.contentRect.height });
    });
    ro.observe(containerRef.current);
    return () => { ro.disconnect(); chart.remove(); chartRef.current = null; };
  }, []);

  const candleDataRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const bbUpRef = useRef<ISeriesApi<'Line'> | null>(null);
  const bbMidRef = useRef<ISeriesApi<'Line'> | null>(null);
  const bbLoRef = useRef<ISeriesApi<'Line'> | null>(null);
  const bb15UpRef = useRef<ISeriesApi<'Line'> | null>(null);
  const bb15MidRef = useRef<ISeriesApi<'Line'> | null>(null);
  const bb15LoRef = useRef<ISeriesApi<'Line'> | null>(null);

  // Update 5m candles + BB
  useEffect(() => {
    if (!chartRef.current || !klines5m?.length) return;
    if (!candleDataRef.current) {
      candleDataRef.current = chartRef.current.addCandlestickSeries({
        upColor: '#10b981', downColor: '#ef4444',
        borderUpColor: '#10b981', borderDownColor: '#ef4444',
        wickUpColor: '#10b981', wickDownColor: '#ef4444',
        priceFormat: { type: 'price', precision: 0, minMove: 1 },
      });
      bbUpRef.current = chartRef.current.addLineSeries({ color: 'rgba(249, 115, 22, 0.6)', lineWidth: 1, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false });
      bbMidRef.current = chartRef.current.addLineSeries({ color: 'rgba(156, 163, 175, 0.4)', lineWidth: 1, lineStyle: LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false });
      bbLoRef.current = chartRef.current.addLineSeries({ color: 'rgba(123, 255, 158, 0.6)', lineWidth: 1, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false });
    }
    const candles = klines5m.map(k => ({
      time: Math.floor(+k[0] / 1000) as any,
      open: +k[1], high: +k[2], low: +k[3], close: +k[4],
    }));
    candleDataRef.current.setData(candles);
    const closes = klines5m.map(k => +k[4]);
    const bb = bollinger(closes, 20, 2.0);
    const tmap = candles.map(c => c.time);
    bbUpRef.current!.setData(tmap.map((t, i) => bb.up[i] != null ? { time: t, value: bb.up[i]! } : null).filter(Boolean) as any);
    bbMidRef.current!.setData(tmap.map((t, i) => bb.mid[i] != null ? { time: t, value: bb.mid[i]! } : null).filter(Boolean) as any);
    bbLoRef.current!.setData(tmap.map((t, i) => bb.lo[i] != null ? { time: t, value: bb.lo[i]! } : null).filter(Boolean) as any);
    chartRef.current.timeScale().fitContent();
  }, [klines5m]);

  // Update 15m BB
  useEffect(() => {
    if (!chartRef.current || !klines15m?.length) return;
    if (!bb15UpRef.current) {
      bb15UpRef.current = chartRef.current.addLineSeries({ color: 'rgba(239, 68, 68, 0.9)', lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      bb15MidRef.current = chartRef.current.addLineSeries({ color: 'rgba(76, 201, 255, 0.7)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      bb15LoRef.current = chartRef.current.addLineSeries({ color: 'rgba(46, 204, 111, 0.9)', lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    }
    const closes15 = klines15m.map(k => +k[4]);
    const bb = bollinger(closes15, 20, 2.0);
    const data = klines15m.map((k, i) => ({ time: Math.floor(+k[0] / 1000) as any, ...bb })).filter((_, i) => bb.up[i] != null);
    bb15UpRef.current!.setData(klines15m.map((k, i) => bb.up[i] != null ? { time: Math.floor(+k[0] / 1000) as any, value: bb.up[i]! } : null).filter(Boolean) as any);
    bb15MidRef.current!.setData(klines15m.map((k, i) => bb.mid[i] != null ? { time: Math.floor(+k[0] / 1000) as any, value: bb.mid[i]! } : null).filter(Boolean) as any);
    bb15LoRef.current!.setData(klines15m.map((k, i) => bb.lo[i] != null ? { time: Math.floor(+k[0] / 1000) as any, value: bb.lo[i]! } : null).filter(Boolean) as any);
  }, [klines15m]);

  return (
    <div class="card p-0 overflow-hidden">
      <div class="px-4 py-3 border-b border-bg-border flex items-center justify-between">
        <span class="text-sm font-semibold uppercase tracking-wide text-text-muted">5m chart</span>
        <span class="text-xs text-text-dim">
          5m BB <span class="text-accent-orange">━━</span> · 15m BB <span class="text-accent-red">━━</span>
        </span>
      </div>
      <div ref={containerRef} class="w-full" style={{ height: 'min(50vh, 400px)' }} />
    </div>
  );
}
