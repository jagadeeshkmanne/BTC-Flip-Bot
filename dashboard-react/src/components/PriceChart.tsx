import { useEffect, useState, useRef } from 'preact/hooks';
import { createChart, ColorType, LineStyle, CrosshairMode, type IChartApi, type ISeriesApi } from 'lightweight-charts';
import { useKlines } from '@/api/bots';
import clsx from 'clsx';

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

// EMA — matches pandas .ewm(span=n, adjust=False).mean() (the bot's formula).
function ema(closes: number[], n: number): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(null);
  if (closes.length === 0) return out;
  const k = 2 / (n + 1);
  let prev = closes[0];
  out[0] = prev;
  for (let i = 1; i < closes.length; i++) {
    prev = closes[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

type TF = '5m' | '15m' | '1h';
const TIMEFRAMES: TF[] = ['5m', '15m', '1h'];

export function PriceChart() {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [tf, setTf] = useState<TF>('5m');

  // Load more bars (1000) for proper scroll/zoom history
  const { data: klinesMain } = useKlines(tf, 1000);
  const { data: klines15m }  = useKlines('15m', 500);

  // Init chart once — Binance-style: full drag/scroll/pinch-zoom, mouse-wheel zoom,
  // magnetic crosshair, both axes scalable.
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
        rightOffset: 3,                  // tighter right margin (was 12 — too much empty space)
        barSpacing: 6,                   // tighter default bar spacing
        minBarSpacing: 0.5,              // allow strong zoom-out
        lockVisibleTimeRangeOnResize: false,
        rightBarStaysOnScroll: true,    // keep latest bar visible while panning
      },
      rightPriceScale: {
        borderColor: '#1f2937',
        scaleMargins: { top: 0.08, bottom: 0.08 },
        minimumWidth: 60,                // cap label-column width so chart isn't pushed left
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#3b82f6', width: 1, labelBackgroundColor: '#3b82f6' },
        horzLine: { color: '#3b82f6', width: 1, labelBackgroundColor: '#3b82f6' },
      },
      // Interactions: drag-to-pan + axis-drag-to-zoom + pinch.
      // mouseWheel disabled on BOTH scroll and scale → page scroll passes through
      // when wheeling over the chart (user requested 2026-06-05).
      handleScroll: {
        mouseWheel: false,           // wheel = page scroll, not chart pan
        pressedMouseMove: true,      // drag to pan
        horzTouchDrag: true,
        vertTouchDrag: true,
      },
      handleScale: {
        mouseWheel: false,           // wheel = page scroll, not chart zoom
        pinch: true,                 // pinch-zoom on mobile
        axisPressedMouseMove: { time: true, price: true },  // drag axes to zoom
      },
      kineticScroll: { touch: true, mouse: false },
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
  const bbLoRef = useRef<ISeriesApi<'Line'> | null>(null);

  // Update candles + BB (uses selected timeframe).
  // 2026-06-05: stripped chart to just what matters for the bot's decisions —
  //   - candles (price)
  //   - 5m BB upper/lower (volatility envelope; thin dashed)
  // 15m EMAs are added below (the bot's actual trend logic).
  // Removed: 5m BB mid, 15m BB upper/mid/lower (too many lines, confusing).
  useEffect(() => {
    const klines5m = klinesMain;
    if (!chartRef.current || !klines5m?.length) return;
    if (!candleDataRef.current) {
      candleDataRef.current = chartRef.current.addCandlestickSeries({
        upColor: '#0ecb81', downColor: '#f6465d',
        borderUpColor: '#0ecb81', borderDownColor: '#f6465d',
        wickUpColor: '#0ecb81', wickDownColor: '#f6465d',
        priceFormat: { type: 'price', precision: 0, minMove: 1 },
      });
      bbUpRef.current = chartRef.current.addLineSeries({
        color: 'rgba(249, 115, 22, 0.5)', lineWidth: 1, lineStyle: LineStyle.Dashed,
        priceLineVisible: false, lastValueVisible: false,
      });
      bbLoRef.current = chartRef.current.addLineSeries({
        color: 'rgba(123, 255, 158, 0.5)', lineWidth: 1, lineStyle: LineStyle.Dashed,
        priceLineVisible: false, lastValueVisible: false,
      });
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
    bbLoRef.current!.setData(tmap.map((t, i) => bb.lo[i] != null ? { time: t, value: bb.lo[i]! } : null).filter(Boolean) as any);
    // Show last 200 bars by default (user can scroll/zoom from there) instead
    // of fitContent() which would cram all 1000 bars into the viewport.
    const t0 = candles[Math.max(0, candles.length - 200)].time;
    const t1 = candles[candles.length - 1].time;
    chartRef.current.timeScale().setVisibleRange({ from: t0 as any, to: t1 as any });
  }, [klinesMain]);

  // 15m EMA20/EMA50 — THE bot's trend gate. Gold above magenta = UP, below = DOWN.
  const ema20Ref = useRef<ISeriesApi<'Line'> | null>(null);
  const ema50Ref = useRef<ISeriesApi<'Line'> | null>(null);

  useEffect(() => {
    if (!chartRef.current || !klines15m?.length) return;
    if (!ema20Ref.current) {
      // 2026-06-08: lastValueVisible=false on EMAs — labels were eating ~120px
      // of right-side width on mobile, pushing the price action off-screen.
      // The legend in the chart header already names these lines.
      ema20Ref.current = chartRef.current.addLineSeries({
        color: '#fcd535', lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
        title: 'EMA20',
      });
      ema50Ref.current = chartRef.current.addLineSeries({
        color: '#e879f9', lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
        title: 'EMA50',
      });
    }
    const closes15 = klines15m.map(k => +k[4]);
    const ema20 = ema(closes15, 20);
    const ema50 = ema(closes15, 50);
    const times = klines15m.map(k => Math.floor(+k[0] / 1000) as any);
    ema20Ref.current!.setData(times.map((t, i) => ema20[i] != null ? { time: t, value: ema20[i]! } : null).filter(Boolean) as any);
    ema50Ref.current!.setData(times.map((t, i) => ema50[i] != null ? { time: t, value: ema50[i]! } : null).filter(Boolean) as any);
  }, [klines15m]);

  return (
    <div class="card p-0 overflow-hidden">
      <div class="section-head">
        <div class="flex items-center gap-3">
          <span class="section-title">BTCUSDT</span>
          {/* Timeframe toggle — Binance-style */}
          <div class="flex items-center gap-0.5 bg-bg p-0.5 rounded">
            {TIMEFRAMES.map(t => (
              <button
                key={t}
                onClick={() => setTf(t)}
                class={clsx(
                  'px-2 py-0.5 rounded text-xs font-medium transition-colors',
                  tf === t
                    ? 'bg-bg-hover text-text'
                    : 'text-text-muted hover:text-text'
                )}
              >{t}</button>
            ))}
          </div>
        </div>
        <span class="text-2xs text-text-dim hidden md:inline">
          <span style="color:#fcd535">━━ 15m EMA20</span> · <span style="color:#e879f9">━━ EMA50</span> ·
          <span style="color:#f97316"> ┄┄ </span>/<span style="color:#7bff9e"> ┄┄ </span> 5m BB · drag to pan
        </span>
      </div>
      <div ref={containerRef} class="w-full" style={{ height: 'min(55vh, 500px)' }} />
    </div>
  );
}
