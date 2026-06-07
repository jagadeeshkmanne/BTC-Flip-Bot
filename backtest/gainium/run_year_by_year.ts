/**
 * FAST year-by-year v1 sweep — pure TypeScript, no Gainium.
 * Computes RSI + EMA inline (fast). Tests multiple configs across each year.
 * Goal: find config that's CONSISTENT across bull (2021,24,25) and bear (2022).
 */
import * as fs from 'fs'

const CSV_PATH = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
const INITIAL_BALANCE = 5000
const LEVERAGE = 3
const COMMISSION_PCT = 0.0004
const SLIPPAGE_PCT = 0.0002
const BREAKER_PAUSE_MIN = 15
const DCA_LEVELS = 2

console.log('═══ FAST YEAR-BY-YEAR v1 SWEEP ═══\n')
console.log('1. Loading 5m bars + computing indicators...')

// ─── Load + parse bars ───
const lines = fs.readFileSync(CSV_PATH, 'utf-8').trim().split('\n')
const bars: { time: number; open: number; high: number; low: number; close: number; year: number; dow: number }[] = []
for (let i = 1; i < lines.length; i++) {
  const c = lines[i].split(',')
  const t = new Date(c[0])
  bars.push({
    time: t.getTime(),
    open: +c[1], high: +c[2], low: +c[3], close: +c[4],
    year: t.getUTCFullYear(),
    dow: t.getUTCDay(),
  })
}
console.log(`   ${bars.length} × 5m bars from ${new Date(bars[0].time).toISOString().slice(0,10)} to ${new Date(bars[bars.length-1].time).toISOString().slice(0,10)}`)

// ─── Compute RSI(9) on 5m ───
const RSI_PERIOD = 9
const rsi = new Float64Array(bars.length)
let avgGain = 0, avgLoss = 0
for (let i = 1; i < bars.length; i++) {
  const change = bars[i].close - bars[i-1].close
  const gain = Math.max(change, 0)
  const loss = Math.max(-change, 0)
  if (i <= RSI_PERIOD) {
    avgGain += gain / RSI_PERIOD
    avgLoss += loss / RSI_PERIOD
    rsi[i] = i === RSI_PERIOD ? 100 - (100 / (1 + avgGain / (avgLoss || 1e-10))) : 50
  } else {
    avgGain = (avgGain * (RSI_PERIOD - 1) + gain) / RSI_PERIOD
    avgLoss = (avgLoss * (RSI_PERIOD - 1) + loss) / RSI_PERIOD
    rsi[i] = 100 - (100 / (1 + avgGain / (avgLoss || 1e-10)))
  }
}

// ─── Compute ATR(14) on 5m ───
const atr = new Float64Array(bars.length)
let prevClose = bars[0].close, trSum = 0
for (let i = 0; i < bars.length; i++) {
  const b = bars[i]
  const tr = Math.max(b.high - b.low, Math.abs(b.high - prevClose), Math.abs(b.low - prevClose))
  if (i < 14) { trSum += tr; atr[i] = i === 13 ? trSum / 14 : 0 }
  else atr[i] = (atr[i-1] * 13 + tr) / 14
  prevClose = b.close
}

// ─── Compute 15m EMA20/50 ───
// Build 15m bars
const bars15: { time: number; open: number; high: number; low: number; close: number }[] = []
{
  let bk: any = null
  for (const b of bars) {
    const t15 = Math.floor(b.time / 900000) * 900000
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
}
function emaArr(values: number[], period: number) {
  const k = 2 / (period + 1)
  const out = new Array(values.length).fill(0); out[0] = values[0]
  for (let i = 1; i < values.length; i++) out[i] = values[i] * k + out[i-1] * (1 - k)
  return out
}
const closes15 = bars15.map(b => b.close)
const ema20_15 = emaArr(closes15, 20)
const ema50_15 = emaArr(closes15, 50)
const ema15ByTime = new Map<number, { ema20: number; ema50: number; trend: 'UP'|'DOWN' }>()
for (let i = 0; i < bars15.length; i++) {
  ema15ByTime.set(bars15[i].time, { ema20: ema20_15[i], ema50: ema50_15[i], trend: ema20_15[i] > ema50_15[i] ? 'UP' : 'DOWN' })
}
function get15(time: number) { return ema15ByTime.get(Math.floor(time / 900000) * 900000) ?? null }

console.log(`   ${bars15.length} × 15m bars\n`)
console.log('2. Pre-computing indicators done\n')

// ─── Config interface ───
interface Cfg {
  rsiOversold: number     // LONG when RSI <= this
  rsiOverbought: number   // SHORT when RSI >= this
  gapMinPct: number       // GAP filter %
  atrMaxPct: number       // ATR filter %
  dcaSpacingPct: number
  tpSinglePct: number
  tpDcaPct: number
  slFromWorstPct: number
  weekendMult: number
  dailyMaxLoss: number
  timeSLHours: number     // 0 = off
  smartTimeSL: boolean    // only fire on loss
}

// ─── Simulator ───
function simulateDeal(startIdx: number, side: 'LONG'|'SHORT', cfg: Cfg, balanceMultiplier: number): { exit: number; pnl: number; reason: string } | null {
  const sign = side === 'LONG' ? 1 : -1
  const b0 = bars[startIdx]
  // Slippage: entry at slightly worse price
  const slipMult = side === 'LONG' ? (1 + SLIPPAGE_PCT) : (1 - SLIPPAGE_PCT)
  const l1Price = b0.close * slipMult
  let worstPrice = l1Price, avgPrice = l1Price, legs = 1
  const dcaPrice = side === 'LONG' ? l1Price * (1 - cfg.dcaSpacingPct / 100) : l1Price * (1 + cfg.dcaSpacingPct / 100)

  // Position sizing (with weekend boost + balance compounding)
  const perLegMargin = (INITIAL_BALANCE * 0.95 * LEVERAGE) / LEVERAGE / DCA_LEVELS  // $2,375
  const perLegNotional = perLegMargin * LEVERAGE  // $7,125
  const perLegQty = perLegNotional / l1Price
  const dow = b0.dow
  const weekend = (dow === 0 || dow === 6)
  const qtyMult = weekend ? cfg.weekendMult : 1.0
  let totalQty = perLegQty * qtyMult * balanceMultiplier

  const entryTrend = get15(b0.time)?.trend
  const maxBars = cfg.timeSLHours > 0 ? Math.ceil(cfg.timeSLHours * 60 / 5) : bars.length

  for (let i = startIdx + 1; i < bars.length; i++) {
    const bar = bars[i]
    const barsHeld = i - startIdx

    // 1. DCA L2 fill?
    if (legs === 1) {
      const dcaHit = side === 'LONG' ? (bar.low <= dcaPrice) : (bar.high >= dcaPrice)
      if (dcaHit) {
        legs = 2
        worstPrice = dcaPrice
        avgPrice = (l1Price + dcaPrice) / 2
        const l2Qty = (perLegNotional / dcaPrice) * qtyMult * balanceMultiplier
        totalQty += l2Qty
      }
    }

    // 2. Time-SL (with SMART variant)
    if (cfg.timeSLHours > 0 && barsHeld >= maxBars) {
      const exitPx = bar.close
      const grossPnl = (exitPx - avgPrice) * totalQty * sign
      const fees = exitPx * totalQty * COMMISSION_PCT
      const netPnl = grossPnl - fees
      if (!cfg.smartTimeSL || netPnl < 0) {
        return { exit: bar.time, pnl: netPnl, reason: 'TIME_SL' }
      }
    }

    // 3. TP (adaptive)
    const tpPct = legs === 2 ? cfg.tpDcaPct : cfg.tpSinglePct
    const tpPrice = side === 'LONG' ? avgPrice * (1 + tpPct / 100) : avgPrice * (1 - tpPct / 100)
    const tpHit = side === 'LONG' ? (bar.high >= tpPrice) : (bar.low <= tpPrice)
    if (tpHit) {
      const grossPnl = (tpPrice - avgPrice) * totalQty * sign
      const fees = tpPrice * totalQty * COMMISSION_PCT
      return { exit: bar.time, pnl: grossPnl - fees, reason: 'TP' }
    }

    // 4. SL (BE-after-DCA if L2 filled, else SL from worst)
    const slPrice = legs === 2 ? avgPrice
      : (side === 'LONG' ? worstPrice * (1 - cfg.slFromWorstPct / 100) : worstPrice * (1 + cfg.slFromWorstPct / 100))
    const slHit = side === 'LONG' ? (bar.low <= slPrice) : (bar.high >= slPrice)
    if (slHit) {
      const grossPnl = (slPrice - avgPrice) * totalQty * sign
      const fees = slPrice * totalQty * COMMISSION_PCT
      return { exit: bar.time, pnl: grossPnl - fees, reason: legs === 2 ? 'BE-DCA' : 'SL' }
    }

    // 5. Trend-flip exit (15m EMA reversal)
    const trendNow = get15(bar.time)?.trend
    if (entryTrend && trendNow && trendNow !== entryTrend) {
      const exitPx = bar.close
      const grossPnl = (exitPx - avgPrice) * totalQty * sign
      const fees = exitPx * totalQty * COMMISSION_PCT
      return { exit: bar.time, pnl: grossPnl - fees, reason: 'TREND_FLIP' }
    }
  }
  return null
}

// ─── Run config over a date range, year-by-year ───
function runConfigYearly(cfg: Cfg, yearFrom: number, yearTo: number) {
  // Build entry signal points
  const entries: { time: number; side: 'LONG' | 'SHORT'; idx: number; year: number }[] = []
  let prevLongCondition = false, prevShortCondition = false

  for (let i = 100; i < bars.length; i++) {
    const b = bars[i]
    if (b.year < yearFrom || b.year > yearTo) continue
    // RSI condition
    const rsiLong = rsi[i] <= cfg.rsiOversold
    const rsiShort = rsi[i] >= cfg.rsiOverbought
    // 15m trend condition
    const e = get15(b.time)
    if (!e) continue
    const trendLong = e.trend === 'UP'
    const trendShort = e.trend === 'DOWN'
    // GAP condition
    if (e.ema50 === 0) continue
    const gapPct = Math.abs((e.ema20 - e.ema50) / e.ema50) * 100
    if (gapPct < cfg.gapMinPct) { prevLongCondition = false; prevShortCondition = false; continue }
    // ATR condition
    if (atr[i] === 0) continue
    const atrPct = atr[i] / b.close * 100
    if (atrPct > cfg.atrMaxPct) { prevLongCondition = false; prevShortCondition = false; continue }

    const longCondition = rsiLong && trendLong
    const shortCondition = rsiShort && trendShort
    // Detect FIRST tick of condition (avoid duplicate entries for consecutive bars)
    if (longCondition && !prevLongCondition) entries.push({ time: b.time, side: 'LONG', idx: i, year: b.year })
    if (shortCondition && !prevShortCondition) entries.push({ time: b.time, side: 'SHORT', idx: i, year: b.year })
    prevLongCondition = longCondition
    prevShortCondition = shortCondition
  }

  // Simulate trades with position blocking + cooldown
  // Each year resets balance? No — single compounding $5K start at yearFrom
  let balance = INITIAL_BALANCE
  let peak = balance
  let maxDD = 0
  let openUntil = 0
  let lossCooldownUntil = 0
  const dailyLoss = new Map<string, number>()
  const yearStats = new Map<number, { trades: number; wins: number; losses: number; profit: number; maxDD: number; balanceStart: number; balanceEnd: number; peakInYear: number }>()
  for (let y = yearFrom; y <= yearTo; y++) {
    yearStats.set(y, { trades: 0, wins: 0, losses: 0, profit: 0, maxDD: 0, balanceStart: 0, balanceEnd: 0, peakInYear: 0 })
  }

  let prevYear = yearFrom
  yearStats.get(yearFrom)!.balanceStart = balance
  let yearStartBalance = balance, yearPeak = balance

  for (const e of entries) {
    // Year boundary handling
    if (e.year !== prevYear) {
      // Close out previous year
      const ps = yearStats.get(prevYear)!
      ps.balanceEnd = balance
      ps.peakInYear = yearPeak
      // Start new year
      yearStats.get(e.year)!.balanceStart = balance
      yearStartBalance = balance
      yearPeak = balance
      prevYear = e.year
    }

    if (e.time < openUntil) continue
    if (e.time < lossCooldownUntil) continue
    const day = new Date(e.time).toISOString().slice(0, 10)
    const dl = dailyLoss.get(day) ?? 0
    if (dl <= -cfg.dailyMaxLoss) continue

    const balMult = balance / INITIAL_BALANCE
    const sim = simulateDeal(e.idx, e.side, cfg, balMult)
    if (!sim) continue

    balance += sim.pnl
    if (balance > peak) peak = balance
    if (balance > yearPeak) yearPeak = balance
    const dd = (peak - balance) / peak * 100; if (dd > maxDD) maxDD = dd
    const yearDD = (yearPeak - balance) / yearPeak * 100
    const ys = yearStats.get(e.year)!
    if (yearDD > ys.maxDD) ys.maxDD = yearDD

    ys.trades++
    ys.profit += sim.pnl
    if (sim.pnl > 0) ys.wins++
    else if (sim.pnl < 0) {
      ys.losses++
      lossCooldownUntil = sim.exit + BREAKER_PAUSE_MIN * 60 * 1000
    }
    dailyLoss.set(day, dl + Math.min(0, sim.pnl))
    openUntil = sim.exit
  }
  // Final year close-out
  const ps = yearStats.get(prevYear)!
  ps.balanceEnd = balance
  ps.peakInYear = yearPeak

  return { yearStats, finalBalance: balance, maxDD, totalProfit: balance - INITIAL_BALANCE }
}

// ─── Configs to test ───
const configs: { tag: string; cfg: Cfg }[] = [
  // 1. ORIGINAL v1 (Python's baseline)
  { tag: 'v1 original (Python)',
    cfg: { rsiOversold: 30, rsiOverbought: 70, gapMinPct: 0.25, atrMaxPct: 0.60,
           dcaSpacingPct: 0.5, tpSinglePct: 0.5, tpDcaPct: 0.25, slFromWorstPct: 0.6,
           weekendMult: 2.0, dailyMaxLoss: 200, timeSLHours: 0, smartTimeSL: false } },
  // 2. ORIGINAL v1.1 (Python's baseline + time-SL)
  { tag: 'v1.1 original (6h time-SL)',
    cfg: { rsiOversold: 30, rsiOverbought: 70, gapMinPct: 0.25, atrMaxPct: 0.60,
           dcaSpacingPct: 0.5, tpSinglePct: 0.5, tpDcaPct: 0.25, slFromWorstPct: 0.6,
           weekendMult: 2.0, dailyMaxLoss: 200, timeSLHours: 6, smartTimeSL: false } },
  // 3. v1.1 SMART (6h smart time-SL)
  { tag: 'v1.1 SMART (6h smart)',
    cfg: { rsiOversold: 30, rsiOverbought: 70, gapMinPct: 0.25, atrMaxPct: 0.60,
           dcaSpacingPct: 0.5, tpSinglePct: 0.5, tpDcaPct: 0.25, slFromWorstPct: 0.6,
           weekendMult: 2.0, dailyMaxLoss: 200, timeSLHours: 6, smartTimeSL: true } },
  // 4. MY TUNED v1.2 (the overfit-suspect config)
  { tag: 'v1.2 TUNED (30d overfit)',
    cfg: { rsiOversold: 33, rsiOverbought: 67, gapMinPct: 0.30, atrMaxPct: 0.60,
           dcaSpacingPct: 0.5, tpSinglePct: 0.6, tpDcaPct: 0.30, slFromWorstPct: 0.6,
           weekendMult: 5.0, dailyMaxLoss: 200, timeSLHours: 12, smartTimeSL: true } },
  // 5. LOOSER (more trades)
  { tag: 'LOOSER (RSI 35/65, GAP 0.15%)',
    cfg: { rsiOversold: 35, rsiOverbought: 65, gapMinPct: 0.15, atrMaxPct: 0.60,
           dcaSpacingPct: 0.5, tpSinglePct: 0.5, tpDcaPct: 0.25, slFromWorstPct: 0.6,
           weekendMult: 2.0, dailyMaxLoss: 200, timeSLHours: 6, smartTimeSL: true } },
  // 6. TIGHTER (fewer but higher quality)
  { tag: 'TIGHTER (RSI 25/75, GAP 0.35%)',
    cfg: { rsiOversold: 25, rsiOverbought: 75, gapMinPct: 0.35, atrMaxPct: 0.60,
           dcaSpacingPct: 0.5, tpSinglePct: 0.5, tpDcaPct: 0.25, slFromWorstPct: 0.6,
           weekendMult: 2.0, dailyMaxLoss: 200, timeSLHours: 6, smartTimeSL: true } },
  // 7. MIDDLE (between v1 and v1.2)
  { tag: 'MIDDLE (RSI 32/68, GAP 0.25%)',
    cfg: { rsiOversold: 32, rsiOverbought: 68, gapMinPct: 0.25, atrMaxPct: 0.60,
           dcaSpacingPct: 0.5, tpSinglePct: 0.5, tpDcaPct: 0.25, slFromWorstPct: 0.6,
           weekendMult: 3.0, dailyMaxLoss: 200, timeSLHours: 6, smartTimeSL: true } },
  // 8. WEEKEND 3× ONLY (test if weekend mult matters)
  { tag: 'v1 + Weekend 3×',
    cfg: { rsiOversold: 30, rsiOverbought: 70, gapMinPct: 0.25, atrMaxPct: 0.60,
           dcaSpacingPct: 0.5, tpSinglePct: 0.5, tpDcaPct: 0.25, slFromWorstPct: 0.6,
           weekendMult: 3.0, dailyMaxLoss: 200, timeSLHours: 0, smartTimeSL: false } },
]

// ─── Run all configs ───
const yearFrom = 2021
const yearTo = 2026
console.log(`3. Running ${configs.length} configs year-by-year (${yearFrom}-${yearTo})...\n`)

interface YearResult { year: number; trades: number; wr: number; profit: number; ret: number; maxDD: number; balStart: number; balEnd: number }
interface Result { tag: string; yearly: YearResult[]; finalBalance: number; totalProfit: number; totalRet: number; maxDD: number; consistency: number }
const results: Result[] = []
const t0 = Date.now()
for (const c of configs) {
  const r = runConfigYearly(c.cfg, yearFrom, yearTo)
  const yearly: YearResult[] = []
  for (const [year, ys] of r.yearStats) {
    const yProfit = ys.balanceEnd - ys.balanceStart
    const yRet = ys.balanceStart > 0 ? yProfit / ys.balanceStart * 100 : 0
    const wr = (ys.wins + ys.losses) > 0 ? ys.wins / (ys.wins + ys.losses) * 100 : 0
    yearly.push({ year, trades: ys.trades, wr, profit: yProfit, ret: yRet, maxDD: ys.maxDD, balStart: ys.balanceStart, balEnd: ys.balanceEnd })
  }
  // Consistency: how many years were profitable
  const profitableYears = yearly.filter(y => y.profit > 0).length
  const totalRet = (r.finalBalance - INITIAL_BALANCE) / INITIAL_BALANCE * 100
  results.push({ tag: c.tag, yearly, finalBalance: r.finalBalance, totalProfit: r.totalProfit, totalRet, maxDD: r.maxDD, consistency: profitableYears })
}
console.log(`   Done in ${((Date.now()-t0)/1000).toFixed(1)}s\n`)

// ─── Print results ───
for (const r of results) {
  console.log(`\n╔════════════════════════════════════════════════════════════════╗`)
  console.log(`║  ${r.tag}`.padEnd(65) + '║')
  console.log(`╠════════════════════════════════════════════════════════════════╣`)
  console.log(`║  Final balance:  $${r.finalBalance.toFixed(2).padStart(10)} (${r.totalRet >= 0 ? '+' : ''}${r.totalRet.toFixed(2)}% / 5y compounded)`.padEnd(65) + '║')
  console.log(`║  Max drawdown:   ${r.maxDD.toFixed(2)}%`.padEnd(65) + '║')
  console.log(`║  Consistency:    ${r.consistency}/${r.yearly.length} years profitable`.padEnd(65) + '║')
  console.log(`╠════════════════════════════════════════════════════════════════╣`)
  console.log(`║  Year | Trades | WR     | Profit       | Return  | MaxDD ║`)
  for (const y of r.yearly) {
    if (y.trades === 0 && y.profit === 0) continue
    const profitStr = `$${y.profit.toFixed(2).padStart(8)}`
    const retStr = `${y.ret >= 0 ? '+' : ''}${y.ret.toFixed(1)}%`.padStart(7)
    console.log(`║  ${y.year} |  ${String(y.trades).padStart(4)}  | ${y.wr.toFixed(1).padStart(4)}%  | ${profitStr.padStart(11)} | ${retStr} | ${y.maxDD.toFixed(2).padStart(4)}% ║`)
  }
  console.log(`╚════════════════════════════════════════════════════════════════╝`)
}

console.log(`\n═══ RANKING ═══`)
results.sort((a, b) => b.totalProfit - a.totalProfit)
console.log('Rank | Config                                    | 5y Profit  | 5y Ret%   | MaxDD% | Years Profitable')
console.log('-----+-------------------------------------------+------------+-----------+--------+----------------')
for (let i = 0; i < results.length; i++) {
  const r = results[i]
  console.log(`${String(i+1).padStart(4)} | ${r.tag.padEnd(41)} | $${r.totalProfit.toFixed(0).padStart(8)} | ${r.totalRet.toFixed(1).padStart(6)}% | ${r.maxDD.toFixed(2).padStart(5)}% | ${r.consistency}/${r.yearly.length}`)
}
