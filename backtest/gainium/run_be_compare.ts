/**
 * Compare v1.1 WITH BE-after-DCA vs WITHOUT (rely on smart-time-SL).
 * Uses the validated pure simulator architecture.
 */
import * as fs from 'fs'

const CSV = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
const INITIAL = 5000, LEV = 3, COMM = 0.0004, SLIP = 0.0002
const COOLDOWN_MIN = 15
const DCA_LEVELS = 2, DCA_ADV = 0.005
const TP_L1 = 0.005, TP_DCA = 0.0025
const SL_FROM_WORST = 0.006
const TREND_GAP_MIN = 0.0025
const ATR_MAX = 0.006
const WKD_MULT = 2.0
const DAILY_STOP = 200
const RSI_PERIOD = 9, RSI_OS = 30, RSI_OB = 70
const TIME_SL_BARS = 72
const SMART_TIME_SL = true

const lines = fs.readFileSync(CSV, 'utf-8').trim().split('\n')
const allBars: any[] = []
for (let i = 1; i < lines.length; i++) {
  const c = lines[i].split(',')
  const t = new Date(c[0])
  allBars.push({ time: t.getTime(), open: +c[1], high: +c[2], low: +c[3], close: +c[4], year: t.getUTCFullYear(), dow: t.getUTCDay() })
}

function computeRSI(bars: any[]): Float64Array {
  const rsi = new Float64Array(bars.length)
  let avgG = 0, avgL = 0
  for (let i = 1; i < bars.length; i++) {
    const ch = bars[i].close - bars[i-1].close
    const g = Math.max(ch, 0), l = Math.max(-ch, 0)
    if (i <= RSI_PERIOD) {
      avgG += g / RSI_PERIOD; avgL += l / RSI_PERIOD
      rsi[i] = i === RSI_PERIOD ? 100 - 100/(1 + avgG/(avgL || 1e-10)) : 50
    } else {
      avgG = (avgG * (RSI_PERIOD - 1) + g) / RSI_PERIOD
      avgL = (avgL * (RSI_PERIOD - 1) + l) / RSI_PERIOD
      rsi[i] = 100 - 100/(1 + avgG/(avgL || 1e-10))
    }
  }
  return rsi
}

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

function build15m(bars: any[]) {
  const bars15: any[] = []
  let bk: any = null
  for (const b of bars) {
    const t15 = Math.floor(b.time/900000)*900000
    if (!bk || bk.time !== t15) { if (bk) bars15.push(bk); bk = { time: t15, close: b.close } }
    else bk.close = b.close
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
  const emaByCloseTime = new Map<number, {ema20: number; ema50: number; trend: 'UP'|'DOWN'}>()
  for (let i = 1; i < bars15.length; i++) {
    const closeTime = bars15[i].time + 15 * 60 * 1000
    emaByCloseTime.set(closeTime, { ema20: e20[i], ema50: e50[i], trend: e20[i] > e50[i] ? 'UP' : 'DOWN' })
  }
  return { bars15, emaByCloseTime }
}

interface DealResult { exit: number; pnl: number; reason: string }

function simulate(
  bars: any[],
  entryIdx: number, side: 'LONG'|'SHORT', balMult: number,
  trendAtEntry: 'UP'|'DOWN',
  emaByCloseTime: Map<number, {ema20: number; ema50: number; trend: 'UP'|'DOWN'}>,
  bars15Times: number[],
  USE_BE_AFTER_DCA: boolean,  // ← key knob
): DealResult | null {
  const sign = side === 'LONG' ? 1 : -1
  const entryBar = bars[entryIdx]
  const slipMul = side === 'LONG' ? (1 + SLIP) : (1 - SLIP)
  const l1Price = entryBar.close * slipMul

  const perLegMargin = INITIAL * 0.95 * LEV / LEV / DCA_LEVELS
  const perLegNotional = perLegMargin * LEV
  const dow = entryBar.dow
  const isWeekend = (dow === 0 || dow === 6)
  const wkdMul = isWeekend ? WKD_MULT : 1.0
  const l1Qty = (perLegNotional / l1Price) * wkdMul * balMult

  let worst = l1Price, avg = l1Price, totalQty = l1Qty, legs = 1
  const dcaPrice = side === 'LONG' ? l1Price * (1 - DCA_ADV) : l1Price * (1 + DCA_ADV)

  function getCurrent15m(time: number) {
    let lo = 0, hi = bars15Times.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (bars15Times[mid] + 15 * 60 * 1000 <= time) lo = mid
      else hi = mid - 1
    }
    const closeTime = bars15Times[lo] + 15 * 60 * 1000
    return emaByCloseTime.get(closeTime)
  }

  for (let i = entryIdx + 1; i < bars.length; i++) {
    const b = bars[i]
    const barsHeld = i - entryIdx

    if (legs === 1) {
      const dcaHit = side === 'LONG' ? (b.low <= dcaPrice) : (b.high >= dcaPrice)
      if (dcaHit) {
        legs = 2; worst = dcaPrice
        avg = (l1Price + dcaPrice) / 2
        totalQty += (perLegNotional / dcaPrice) * wkdMul * balMult
        continue
      }
    }

    // TP
    const tpPct = legs === 2 ? TP_DCA : TP_L1
    const tpPx = side === 'LONG' ? avg * (1 + tpPct) : avg * (1 - tpPct)
    const tpHit = side === 'LONG' ? (b.high >= tpPx) : (b.low <= tpPx)
    if (tpHit) {
      const exitPx = tpPx * (side === 'LONG' ? (1 - SLIP) : (1 + SLIP))
      const grossPnl = (exitPx - avg) * totalQty * sign
      const fees = exitPx * totalQty * COMM
      return { exit: b.time, pnl: grossPnl - fees, reason: 'TP' }
    }

    // SL — depends on mode
    let shouldCheckSL = false
    let slPx: number = 0
    if (legs === 1) {
      // L1 only — always check SL at 0.6% from worst
      slPx = side === 'LONG' ? worst * (1 - SL_FROM_WORST) : worst * (1 + SL_FROM_WORST)
      shouldCheckSL = true
    } else if (USE_BE_AFTER_DCA) {
      // BE-after-DCA: SL at avg
      slPx = avg
      shouldCheckSL = true
    }
    // If NO BE-after-DCA: skip SL entirely after L2 fills (rely on time-SL + trend-flip + TP)
    if (shouldCheckSL) {
      const slHit = side === 'LONG' ? (b.low <= slPx) : (b.high >= slPx)
      if (slHit) {
        const exitPx = slPx * (side === 'LONG' ? (1 - SLIP) : (1 + SLIP))
        const grossPnl = (exitPx - avg) * totalQty * sign
        const fees = exitPx * totalQty * COMM
        return { exit: b.time, pnl: grossPnl - fees, reason: legs === 2 ? 'BE-DCA' : 'SL' }
      }
    }

    // Trend-flip
    const cur15 = getCurrent15m(b.time)
    if (cur15 && cur15.trend !== trendAtEntry) {
      const exitPx = b.close * (side === 'LONG' ? (1 - SLIP) : (1 + SLIP))
      const grossPnl = (exitPx - avg) * totalQty * sign
      const fees = exitPx * totalQty * COMM
      return { exit: b.time, pnl: grossPnl - fees, reason: 'TREND_FLIP' }
    }

    // Smart 6h time-SL
    if (barsHeld >= TIME_SL_BARS) {
      const exitPx = b.close * (side === 'LONG' ? (1 - SLIP) : (1 + SLIP))
      const grossPnl = (exitPx - avg) * totalQty * sign
      const fees = exitPx * totalQty * COMM
      const netPnl = grossPnl - fees
      if (!SMART_TIME_SL || netPnl < 0) {
        return { exit: b.time, pnl: netPnl, reason: 'TIME_SL' }
      }
    }
  }
  return null
}

function runWindow(days: number, USE_BE_AFTER_DCA: boolean): any {
  const cutoff = allBars[allBars.length - 1].time - days * 86400 * 1000
  const bars = allBars.filter(b => b.time >= cutoff)
  const rsi = computeRSI(bars)
  const atr = computeATR(bars)
  const { bars15, emaByCloseTime } = build15m(bars)
  const bars15Times = bars15.map((b: any) => b.time)

  function getCurrent15m(time: number) {
    let lo = 0, hi = bars15Times.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (bars15Times[mid] + 15 * 60 * 1000 <= time) lo = mid
      else hi = mid - 1
    }
    const closeTime = bars15Times[lo] + 15 * 60 * 1000
    return emaByCloseTime.get(closeTime)
  }

  let prevLong = false, prevShort = false
  const entries: any[] = []
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

  let balance = INITIAL, peak = balance, maxDD = 0
  let openUntil = 0, coolUntil = 0
  const dailyLoss = new Map<string, number>()
  const reasons: any = {}
  let totalWins = 0, totalLosses = 0, sumWins = 0, sumLosses = 0
  let biggestWin = 0, biggestLoss = 0
  for (const e of entries) {
    const entryBar = bars[e.idx]
    if (entryBar.time < openUntil) continue
    if (entryBar.time < coolUntil) continue
    const day = new Date(entryBar.time).toISOString().slice(0, 10)
    const dl = dailyLoss.get(day) ?? 0
    if (dl <= -DAILY_STOP) continue
    const balMult = balance / INITIAL
    const sim = simulate(bars, e.idx, e.side, balMult, e.trend, emaByCloseTime, bars15Times, USE_BE_AFTER_DCA)
    if (!sim) continue
    balance += sim.pnl
    if (balance > peak) peak = balance
    const dd = (peak - balance) / peak * 100
    if (dd > maxDD) maxDD = dd
    reasons[sim.reason] = (reasons[sim.reason] || 0) + 1
    if (sim.pnl > 0) { totalWins++; sumWins += sim.pnl; if (sim.pnl > biggestWin) biggestWin = sim.pnl }
    else if (sim.pnl < 0) {
      totalLosses++; sumLosses += sim.pnl
      coolUntil = sim.exit + COOLDOWN_MIN * 60 * 1000
      if (sim.pnl < biggestLoss) biggestLoss = sim.pnl
    }
    dailyLoss.set(day, dl + Math.min(0, sim.pnl))
    openUntil = sim.exit
  }
  const totalTrades = totalWins + totalLosses
  const wr = totalTrades > 0 ? totalWins / totalTrades * 100 : 0
  const pf = sumLosses < 0 ? Math.abs(sumWins / sumLosses) : Infinity
  return { trades: totalTrades, wins: totalWins, losses: totalLosses, wr, balance, profit: balance - INITIAL, ret: (balance-INITIAL)/INITIAL*100, maxDD, pf, reasons, biggestWin, biggestLoss }
}

console.log('═══ BE-after-DCA: ON vs OFF (v1.1 SMART with smart 6h time-SL) ═══\n')

for (const days of [30, 60, 90, 180, 365, 365*5]) {
  console.log(`──── ${days}-day window ────`)
  const withBE = runWindow(days, true)
  const noBE = runWindow(days, false)
  console.log(`  WITH BE-after-DCA (current):    ${withBE.trades.toString().padStart(4)} trades | WR ${withBE.wr.toFixed(1)}% | profit $${withBE.profit.toFixed(0)} | DD ${withBE.maxDD.toFixed(2)}% | PF ${withBE.pf.toFixed(2)} | biggest -$${(-withBE.biggestLoss).toFixed(0)}`)
  console.log(`  WITHOUT BE-after-DCA (proposed): ${noBE.trades.toString().padStart(4)} trades | WR ${noBE.wr.toFixed(1)}% | profit $${noBE.profit.toFixed(0)} | DD ${noBE.maxDD.toFixed(2)}% | PF ${noBE.pf.toFixed(2)} | biggest -$${(-noBE.biggestLoss).toFixed(0)}`)
  console.log(`  Difference:                      ${noBE.profit > withBE.profit ? '+' : ''}$${(noBE.profit - withBE.profit).toFixed(0)} | DD change: ${(noBE.maxDD - withBE.maxDD > 0 ? '+' : '')}${(noBE.maxDD - withBE.maxDD).toFixed(2)}%`)
  console.log(`  WITH BE exits:    TP=${withBE.reasons.TP||0}, BE-DCA=${withBE.reasons['BE-DCA']||0}, SL=${withBE.reasons.SL||0}, TF=${withBE.reasons.TREND_FLIP||0}, TIME=${withBE.reasons.TIME_SL||0}`)
  console.log(`  WITHOUT BE exits: TP=${noBE.reasons.TP||0}, BE-DCA=${noBE.reasons['BE-DCA']||0}, SL=${noBE.reasons.SL||0}, TF=${noBE.reasons.TREND_FLIP||0}, TIME=${noBE.reasons.TIME_SL||0}`)
  console.log()
}

// ─── BONUS: test for v1 (no time-SL backup) ───
console.log('\n═══ v1 ONLY (no time-SL): BE-DCA ON vs OFF ═══\n')
console.log('(if BE-DCA OFF, position holds until TP/0.6%-SL/trend-flip only)\n')

// Patch: modify TIME_SL_BARS to disable smart-time-SL for this test
const ORIGINAL_TIME_SL = (globalThis as any).__TIME_SL_OVERRIDE
;(globalThis as any).__TIME_SL_OVERRIDE = -1  // signal to disable

// Recreate runWindow with timeSL disabled
function runWindowNoTimeSL(days: number, USE_BE_AFTER_DCA: boolean): any {
  const cutoff = allBars[allBars.length - 1].time - days * 86400 * 1000
  const bars = allBars.filter(b => b.time >= cutoff)
  const rsi = computeRSI(bars)
  const atr = computeATR(bars)
  const { bars15, emaByCloseTime } = build15m(bars)
  const bars15Times = bars15.map((b: any) => b.time)

  function getCurrent15m(time: number) {
    let lo = 0, hi = bars15Times.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (bars15Times[mid] + 15 * 60 * 1000 <= time) lo = mid
      else hi = mid - 1
    }
    const closeTime = bars15Times[lo] + 15 * 60 * 1000
    return emaByCloseTime.get(closeTime)
  }

  let prevLong = false, prevShort = false
  const entries: any[] = []
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

  // Inline simulate with no time-SL
  function simV1(entryIdx: number, side: 'LONG'|'SHORT', balMult: number, trend0: 'UP'|'DOWN') {
    const sign = side === 'LONG' ? 1 : -1
    const entryBar = bars[entryIdx]
    const slipMul = side === 'LONG' ? (1 + SLIP) : (1 - SLIP)
    const l1Price = entryBar.close * slipMul
    const perLegNotional = (INITIAL * 0.95 * LEV) / DCA_LEVELS
    const dow = entryBar.dow
    const wkdMul = (dow === 0 || dow === 6) ? WKD_MULT : 1.0
    let worst = l1Price, avg = l1Price, totalQty = (perLegNotional / l1Price) * wkdMul * balMult, legs = 1
    const dcaPrice = side === 'LONG' ? l1Price * (1 - DCA_ADV) : l1Price * (1 + DCA_ADV)

    for (let i = entryIdx + 1; i < bars.length; i++) {
      const b = bars[i]
      if (legs === 1) {
        const dcaHit = side === 'LONG' ? (b.low <= dcaPrice) : (b.high >= dcaPrice)
        if (dcaHit) { legs = 2; worst = dcaPrice; avg = (l1Price + dcaPrice) / 2; totalQty += (perLegNotional / dcaPrice) * wkdMul * balMult; continue }
      }
      const tpPct = legs === 2 ? TP_DCA : TP_L1
      const tpPx = side === 'LONG' ? avg * (1 + tpPct) : avg * (1 - tpPct)
      const tpHit = side === 'LONG' ? (b.high >= tpPx) : (b.low <= tpPx)
      if (tpHit) {
        const exitPx = tpPx * (side === 'LONG' ? (1 - SLIP) : (1 + SLIP))
        return { exit: b.time, pnl: (exitPx - avg) * totalQty * sign - exitPx * totalQty * COMM, reason: 'TP' }
      }
      // SL logic depends on USE_BE_AFTER_DCA
      let slPx: number | null = null
      if (legs === 1) slPx = side === 'LONG' ? worst * (1 - SL_FROM_WORST) : worst * (1 + SL_FROM_WORST)
      else if (USE_BE_AFTER_DCA) slPx = avg
      else slPx = side === 'LONG' ? worst * (1 - SL_FROM_WORST) : worst * (1 + SL_FROM_WORST)  // L2-from-worst SL
      const slHit = side === 'LONG' ? (b.low <= slPx) : (b.high >= slPx)
      if (slHit) {
        const exitPx = slPx * (side === 'LONG' ? (1 - SLIP) : (1 + SLIP))
        return { exit: b.time, pnl: (exitPx - avg) * totalQty * sign - exitPx * totalQty * COMM, reason: legs === 2 ? (USE_BE_AFTER_DCA ? 'BE-DCA' : 'SL-L2-WORST') : 'SL' }
      }
      const cur15 = getCurrent15m(b.time)
      if (cur15 && cur15.trend !== trend0) {
        const exitPx = b.close * (side === 'LONG' ? (1 - SLIP) : (1 + SLIP))
        return { exit: b.time, pnl: (exitPx - avg) * totalQty * sign - exitPx * totalQty * COMM, reason: 'TREND_FLIP' }
      }
      // NO TIME-SL in v1
    }
    return null
  }

  let balance = INITIAL, peak = balance, maxDD = 0
  let openUntil = 0, coolUntil = 0
  const dailyLoss = new Map<string, number>()
  const reasons: any = {}
  let totalWins = 0, totalLosses = 0, sumWins = 0, sumLosses = 0
  let biggestLoss = 0
  for (const e of entries) {
    const entryBar = bars[e.idx]
    if (entryBar.time < openUntil) continue
    if (entryBar.time < coolUntil) continue
    const day = new Date(entryBar.time).toISOString().slice(0, 10)
    const dl = dailyLoss.get(day) ?? 0
    if (dl <= -DAILY_STOP) continue
    const sim = simV1(e.idx, e.side, balance/INITIAL, e.trend)
    if (!sim) continue
    balance += sim.pnl
    if (balance > peak) peak = balance
    const dd = (peak - balance) / peak * 100; if (dd > maxDD) maxDD = dd
    reasons[sim.reason] = (reasons[sim.reason] || 0) + 1
    if (sim.pnl > 0) { totalWins++; sumWins += sim.pnl }
    else if (sim.pnl < 0) { totalLosses++; sumLosses += sim.pnl; coolUntil = sim.exit + COOLDOWN_MIN * 60 * 1000; if (sim.pnl < biggestLoss) biggestLoss = sim.pnl }
    dailyLoss.set(day, dl + Math.min(0, sim.pnl))
    openUntil = sim.exit
  }
  const t = totalWins + totalLosses
  return { trades: t, wins: totalWins, losses: totalLosses, wr: t>0 ? totalWins/t*100 : 0, profit: balance - INITIAL, maxDD, pf: sumLosses < 0 ? Math.abs(sumWins/sumLosses) : Infinity, reasons, biggestLoss }
}

for (const days of [30, 60, 90, 180, 365]) {
  console.log(`──── ${days}-day window ────`)
  const withBE = runWindowNoTimeSL(days, true)
  const noBE = runWindowNoTimeSL(days, false)
  console.log(`  v1 WITH BE-after-DCA:    ${withBE.trades.toString().padStart(4)} trades | WR ${withBE.wr.toFixed(1)}% | profit $${withBE.profit.toFixed(0)} | DD ${withBE.maxDD.toFixed(2)}% | PF ${withBE.pf.toFixed(2)} | biggest -$${(-withBE.biggestLoss).toFixed(0)}`)
  console.log(`  v1 WITHOUT BE (SL@worst): ${noBE.trades.toString().padStart(4)} trades | WR ${noBE.wr.toFixed(1)}% | profit $${noBE.profit.toFixed(0)} | DD ${noBE.maxDD.toFixed(2)}% | PF ${noBE.pf.toFixed(2)} | biggest -$${(-noBE.biggestLoss).toFixed(0)}`)
  console.log(`  Difference: $${(noBE.profit - withBE.profit).toFixed(0)} | DD: ${(noBE.maxDD - withBE.maxDD).toFixed(2)}%`)
  console.log(`  WITH BE exits:    ${JSON.stringify(withBE.reasons)}`)
  console.log(`  WITHOUT BE exits: ${JSON.stringify(noBE.reasons)}`)
  console.log()
}
