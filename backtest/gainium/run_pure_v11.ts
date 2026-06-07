/**
 * PURE v1.1 SMART BACKTESTER — no Gainium, no shortcuts.
 * Strict implementation of LIVE bot's exact entry + exit logic.
 *
 * Validated against 2 independent agents (Python + fresh TS) on 60d:
 * If this matches their ~80 trade count and ~48% WR, the spec is correct.
 * If not, find the bug.
 */
import * as fs from 'fs'

const CSV = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
const INITIAL = 5000
const LEV = 3
const COMM = 0.0004           // 0.04% taker fee per side
const SLIP = 0.0002           // 0.02% slippage per side
const COOLDOWN_MIN = 15
const DCA_LEVELS = 2
const DCA_ADV = 0.005         // 0.5% adverse for L2
const TP_L1 = 0.005           // 0.5% from avg (L1 only)
const TP_DCA = 0.0025         // 0.25% from avg (post-DCA)
const SL_FROM_WORST = 0.006   // 0.6% from worst entry (L1 only)
const TREND_GAP_MIN = 0.0025  // 0.25% min GAP
const ATR_MAX = 0.006         // 0.6% max ATR
const WKD_MULT = 2.0
const DAILY_STOP = 200
const RSI_PERIOD = 9
const RSI_OS = 30
const RSI_OB = 70
const TIME_SL_BARS = 72       // 6h
const SMART_TIME_SL = true

// ─── Load data ───
const lines = fs.readFileSync(CSV, 'utf-8').trim().split('\n')
const allBars: any[] = []
for (let i = 1; i < lines.length; i++) {
  const c = lines[i].split(',')
  const t = new Date(c[0])
  allBars.push({
    time: t.getTime(),
    open: +c[1], high: +c[2], low: +c[3], close: +c[4],
    year: t.getUTCFullYear(), dow: t.getUTCDay(),
  })
}

// ─── Compute RSI(9) Wilder ───
function computeRSI(bars: any[]): Float64Array {
  const rsi = new Float64Array(bars.length)
  let avgG = 0, avgL = 0
  for (let i = 1; i < bars.length; i++) {
    const ch = bars[i].close - bars[i-1].close
    const g = Math.max(ch, 0), l = Math.max(-ch, 0)
    if (i <= RSI_PERIOD) {
      avgG += g / RSI_PERIOD
      avgL += l / RSI_PERIOD
      rsi[i] = i === RSI_PERIOD ? 100 - 100/(1 + avgG/(avgL || 1e-10)) : 50
    } else {
      avgG = (avgG * (RSI_PERIOD - 1) + g) / RSI_PERIOD
      avgL = (avgL * (RSI_PERIOD - 1) + l) / RSI_PERIOD
      rsi[i] = 100 - 100/(1 + avgG/(avgL || 1e-10))
    }
  }
  return rsi
}

// ─── Compute ATR(14) Wilder ───
function computeATR(bars: any[]): Float64Array {
  const atr = new Float64Array(bars.length)
  let pc = bars[0].close, sum = 0
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i]
    const tr = Math.max(b.high - b.low, Math.abs(b.high - pc), Math.abs(b.low - pc))
    if (i < 14) { sum += tr; atr[i] = i === 13 ? sum/14 : 0 }
    else atr[i] = (atr[i-1] * 13 + tr) / 14
    pc = b.close
  }
  return atr
}

// ─── Build 15m bars + EMA ───
function build15mEMA(bars: any[]) {
  const bars15: any[] = []
  let bk: any = null
  for (const b of bars) {
    const t15 = Math.floor(b.time/900000)*900000
    if (!bk || bk.time !== t15) {
      if (bk) bars15.push(bk)
      bk = { time: t15, open: b.open, high: b.high, low: b.low, close: b.close }
    } else {
      bk.high = Math.max(bk.high, b.high)
      bk.low = Math.min(bk.low, b.low)
      bk.close = b.close
    }
  }
  if (bk) bars15.push(bk)

  function ema(values: number[], period: number) {
    const k = 2 / (period + 1)
    const out = new Array(values.length).fill(0); out[0] = values[0]
    for (let i = 1; i < values.length; i++) out[i] = values[i] * k + out[i-1] * (1 - k)
    return out
  }
  const closes = bars15.map((b: any) => b.close)
  const e20 = ema(closes, 20)
  const e50 = ema(closes, 50)
  // Map: 15m bucket time -> {ema20, ema50, trend}
  // CRITICAL: use the LAST CLOSED 15m bar — bar i closes at time[i] + 15min
  // For 5m bar at time T, we look up 15m bar at floor(T/15m)*15m
  // But that 15m bar is STILL OPEN at time T (in real life). So we use the PREVIOUS closed bar.
  const emaByTime = new Map<number, {ema20: number; ema50: number; trend: 'UP'|'DOWN'}>()
  for (let i = 1; i < bars15.length; i++) {
    // 15m bar [i] CLOSES at bars15[i].time + 15min
    // From bars15[i].time + 15min onwards, we know the indicator value of bar [i]
    // So the EMA available at time T = ema of bar i where bar i closed BEFORE T
    const closeTime = bars15[i].time + 15 * 60 * 1000
    emaByTime.set(closeTime, { ema20: e20[i], ema50: e50[i], trend: e20[i] > e50[i] ? 'UP' : 'DOWN' })
  }
  // Build a fast lookup: for any 5m time, what's the latest closed 15m EMA?
  // We pre-compute by walking through 5m bars
  return { bars15, e20, e50, emaByTime }
}

// ─── Find available indicator value for a 5m bar time ───
function lookup15m(time: number, lastClosed: { time: number; ema20: number; ema50: number; trend: 'UP'|'DOWN' } | null) {
  return lastClosed
}

// ─── Simulate one deal ───
interface DealResult { exit: number; pnl: number; reason: string; legs: number; weekend: boolean; entry: number; barsHeld: number }

function simulate(
  bars: any[], rsi: Float64Array, atr: Float64Array,
  entryIdx: number, side: 'LONG'|'SHORT', balMult: number,
  trendAtEntry: 'UP'|'DOWN',
  emaByTime: Map<number, {ema20: number; ema50: number; trend: 'UP'|'DOWN'}>,
  bars15Times: number[]
): DealResult | null {
  const sign = side === 'LONG' ? 1 : -1
  const entryBar = bars[entryIdx]

  // Slippage on entry
  const slipMul = side === 'LONG' ? (1 + SLIP) : (1 - SLIP)
  const l1Price = entryBar.close * slipMul

  // Position sizing
  const perLegMargin = INITIAL * 0.95 * LEV / LEV / DCA_LEVELS  // $2,375
  const perLegNotional = perLegMargin * LEV  // $7,125
  const dow = entryBar.dow
  const isWeekend = (dow === 0 || dow === 6)
  const wkdMul = isWeekend ? WKD_MULT : 1.0
  const l1Qty = (perLegNotional / l1Price) * wkdMul * balMult

  let worst = l1Price
  let avg = l1Price
  let totalQty = l1Qty
  let legs = 1
  const dcaPrice = side === 'LONG' ? l1Price * (1 - DCA_ADV) : l1Price * (1 + DCA_ADV)

  // Track last closed 15m for trend-flip exit
  function getCurrent15m(time: number) {
    // Find the latest 15m bar whose close <= time
    // Closed 15m bar at index i closes at bars15Times[i] + 15min
    // We want max i such that bars15Times[i] + 15min <= time
    let lo = 0, hi = bars15Times.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (bars15Times[mid] + 15 * 60 * 1000 <= time) lo = mid
      else hi = mid - 1
    }
    const closeTime = bars15Times[lo] + 15 * 60 * 1000
    return emaByTime.get(closeTime)
  }

  for (let i = entryIdx + 1; i < bars.length; i++) {
    const b = bars[i]
    const barsHeld = i - entryIdx

    // 1. DCA L2 fill check (priority — happens before TP/SL)
    if (legs === 1) {
      const dcaHit = side === 'LONG' ? (b.low <= dcaPrice) : (b.high >= dcaPrice)
      if (dcaHit) {
        legs = 2
        worst = dcaPrice
        avg = (l1Price + dcaPrice) / 2
        const l2Qty = (perLegNotional / dcaPrice) * wkdMul * balMult
        totalQty += l2Qty
        // Don't process other exits on same bar as DCA (pessimistic)
        continue
      }
    }

    // 2. TP check (adaptive: 0.5% L1 / 0.25% post-DCA)
    const tpPct = legs === 2 ? TP_DCA : TP_L1
    const tpPx = side === 'LONG' ? avg * (1 + tpPct) : avg * (1 - tpPct)
    const tpHit = side === 'LONG' ? (b.high >= tpPx) : (b.low <= tpPx)
    if (tpHit) {
      // Slip on exit
      const exitPx = tpPx * (side === 'LONG' ? (1 - SLIP) : (1 + SLIP))
      const grossPnl = (exitPx - avg) * totalQty * sign
      const fees = exitPx * totalQty * COMM
      return { exit: b.time, pnl: grossPnl - fees, reason: 'TP', legs, weekend: isWeekend, entry: l1Price, barsHeld }
    }

    // 3. SL check
    let slPx: number
    if (legs === 2) {
      slPx = avg  // BE-after-DCA
    } else {
      slPx = side === 'LONG' ? worst * (1 - SL_FROM_WORST) : worst * (1 + SL_FROM_WORST)
    }
    const slHit = side === 'LONG' ? (b.low <= slPx) : (b.high >= slPx)
    if (slHit) {
      const exitPx = slPx * (side === 'LONG' ? (1 - SLIP) : (1 + SLIP))
      const grossPnl = (exitPx - avg) * totalQty * sign
      const fees = exitPx * totalQty * COMM
      const reason = legs === 2 ? 'BE-DCA' : 'SL'
      return { exit: b.time, pnl: grossPnl - fees, reason, legs, weekend: isWeekend, entry: l1Price, barsHeld }
    }

    // 4. Trend-flip exit
    const cur15 = getCurrent15m(b.time)
    if (cur15 && cur15.trend !== trendAtEntry) {
      const exitPx = b.close * (side === 'LONG' ? (1 - SLIP) : (1 + SLIP))
      const grossPnl = (exitPx - avg) * totalQty * sign
      const fees = exitPx * totalQty * COMM
      return { exit: b.time, pnl: grossPnl - fees, reason: 'TREND_FLIP', legs, weekend: isWeekend, entry: l1Price, barsHeld }
    }

    // 5. Smart 6h time-SL
    if (barsHeld >= TIME_SL_BARS) {
      const exitPx = b.close * (side === 'LONG' ? (1 - SLIP) : (1 + SLIP))
      const grossPnl = (exitPx - avg) * totalQty * sign
      const fees = exitPx * totalQty * COMM
      const netPnl = grossPnl - fees
      if (!SMART_TIME_SL || netPnl < 0) {
        return { exit: b.time, pnl: netPnl, reason: 'TIME_SL', legs, weekend: isWeekend, entry: l1Price, barsHeld }
      }
    }
  }
  return null
}

// ─── Main run function ───
function runWindow(days: number, label: string) {
  // Use last N days
  const cutoff = allBars[allBars.length - 1].time - days * 86400 * 1000
  const bars = allBars.filter(b => b.time >= cutoff)
  const startDate = new Date(bars[0].time).toISOString().slice(0, 10)
  const endDate = new Date(bars[bars.length - 1].time).toISOString().slice(0, 10)

  const rsi = computeRSI(bars)
  const atr = computeATR(bars)
  const { bars15, e20, e50, emaByTime } = build15mEMA(bars)
  const bars15Times = bars15.map((b: any) => b.time)

  // Build a lookup of "last closed 15m EMA" for each 5m bar
  // This gives us the EMA value that would be available at that 5m bar's time
  function getCurrent15m(time: number) {
    let lo = 0, hi = bars15Times.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (bars15Times[mid] + 15 * 60 * 1000 <= time) lo = mid
      else hi = mid - 1
    }
    const closeTime = bars15Times[lo] + 15 * 60 * 1000
    return emaByTime.get(closeTime)
  }

  // Find entry signals (transition from false to true)
  let prevLong = false, prevShort = false
  const entries: { idx: number; side: 'LONG'|'SHORT'; trend: 'UP'|'DOWN' }[] = []

  for (let i = 100; i < bars.length; i++) {
    const b = bars[i]
    const e = getCurrent15m(b.time)
    if (!e || e.ema50 === 0) { prevLong = false; prevShort = false; continue }
    const gapAbs = Math.abs((e.ema20 - e.ema50) / e.ema50)
    if (gapAbs < TREND_GAP_MIN) { prevLong = false; prevShort = false; continue }
    if (atr[i] === 0) continue
    if ((atr[i] / b.close) > ATR_MAX) { prevLong = false; prevShort = false; continue }

    const longCond = rsi[i] <= RSI_OS && e.trend === 'UP'
    const shortCond = rsi[i] >= RSI_OB && e.trend === 'DOWN'

    if (longCond && !prevLong) entries.push({ idx: i, side: 'LONG', trend: e.trend })
    if (shortCond && !prevShort) entries.push({ idx: i, side: 'SHORT', trend: e.trend })
    prevLong = longCond
    prevShort = shortCond
  }

  // Simulate trades
  let balance = INITIAL
  let peak = balance, maxDD = 0
  let openUntil = 0, coolUntil = 0
  const dailyLoss = new Map<string, number>()
  const reasonStats: any = {}
  const results: DealResult[] = []
  let totalWins = 0, totalLosses = 0
  let sumWins = 0, sumLosses = 0
  let totalHold = 0

  for (const e of entries) {
    const entryBar = bars[e.idx]
    if (entryBar.time < openUntil) continue
    if (entryBar.time < coolUntil) continue
    const day = new Date(entryBar.time).toISOString().slice(0, 10)
    const dl = dailyLoss.get(day) ?? 0
    if (dl <= -DAILY_STOP) continue

    const balMult = balance / INITIAL
    const sim = simulate(bars, rsi, atr, e.idx, e.side, balMult, e.trend, emaByTime, bars15Times)
    if (!sim) continue

    balance += sim.pnl
    if (balance > peak) peak = balance
    const dd = (peak - balance) / peak * 100
    if (dd > maxDD) maxDD = dd

    reasonStats[sim.reason] = (reasonStats[sim.reason] || 0) + 1
    results.push(sim)
    totalHold += (sim.exit - entryBar.time)

    if (sim.pnl > 0) { totalWins++; sumWins += sim.pnl }
    else if (sim.pnl < 0) {
      totalLosses++; sumLosses += sim.pnl
      coolUntil = sim.exit + COOLDOWN_MIN * 60 * 1000
    }
    dailyLoss.set(day, dl + Math.min(0, sim.pnl))
    openUntil = sim.exit
  }

  const totalTrades = totalWins + totalLosses
  const wr = totalTrades > 0 ? totalWins / totalTrades * 100 : 0
  const pf = sumLosses < 0 ? Math.abs(sumWins / sumLosses) : Infinity
  const avgHold = totalTrades > 0 ? totalHold / totalTrades / (60 * 60 * 1000) : 0

  console.log(`\n══════════════════════════════════════════════════════════════════`)
  console.log(`  ${label}`)
  console.log(`  Window: ${startDate} → ${endDate} (${days} days, ${bars.length} bars)`)
  console.log(`══════════════════════════════════════════════════════════════════`)
  console.log(`  Raw entry signals:  ${entries.length}`)
  console.log(`  Active trades:      ${totalTrades}`)
  console.log(`  Wins / Losses:      ${totalWins} / ${totalLosses}`)
  console.log(`  Win rate:           ${wr.toFixed(1)}%`)
  console.log(`  Net profit:         $${(balance - INITIAL).toFixed(2)} (${((balance-INITIAL)/INITIAL*100).toFixed(2)}%)`)
  console.log(`  Max drawdown:       ${maxDD.toFixed(2)}%`)
  console.log(`  Profit factor:      ${pf.toFixed(2)}`)
  console.log(`  Avg hold time:      ${avgHold.toFixed(2)}h`)
  console.log(`  Sum of wins:        $${sumWins.toFixed(2)}`)
  console.log(`  Sum of losses:      $${sumLosses.toFixed(2)}`)
  console.log(`  Exit breakdown:`)
  for (const [reason, count] of Object.entries(reasonStats)) {
    console.log(`    ${reason.padEnd(12)} ${String(count).padStart(4)}`)
  }
}

console.log('═══ PURE v1.1 SMART BACKTESTER (no Gainium, strict spec) ═══')

runWindow(30, '1️⃣  30-day window')
runWindow(60, '2️⃣  60-day window (matches agent comparison)')
runWindow(90, '3️⃣  90-day window')
runWindow(180, '4️⃣  180-day window')
runWindow(365, '5️⃣  365-day window')
runWindow(365 * 5, '6️⃣  5-year window')
