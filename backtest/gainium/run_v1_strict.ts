/**
 * v1 / v1.1 STRICT — Uses Gainium ONLY for entry detection,
 * replays exit logic exactly as deployed Python v1 does:
 *
 *   - Adaptive TP: 0.5% L1-only / 0.25% post-DCA from avg
 *   - SL from WORST entry (not start/avg)
 *   - BE-after-DCA: SL → avg when L2 fires
 *   - Trend-flip exit: close on 15m EMA20/50 reversal
 *   - v1.1: + 6h time-SL
 *
 * Per-bar walking matches v1's processBar logic exactly.
 */

import DCABacktesting from './src/dca'
import {
  ExchangeIntervals, ExchangeEnum, StrategyEnum,
  OrderTypeEnum, StartConditionEnum, OrderSizeTypeEnum,
  IndicatorEnum, IndicatorStartConditionEnum, IndicatorsLogicEnum,
  IndicatorAction, IndicatorSection, MAEnum,
  CooldownUnits, CloseConditionEnum,
} from './src/types'
import * as fs from 'fs'
import { v4 as uuidv4 } from 'uuid'

// ═════════════════════════ EXACT v1 CONFIG ═════════════════════════
const CSV_PATH = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
const TEST_DAYS = parseInt(process.env.TEST_DAYS || '30')

// ─── ORIGINAL v1 CONFIG (Python's +269%/5y claim) ───
const INITIAL_BALANCE = 5000
const LEVERAGE = 3
const DCA_LEVELS = 2
const DCA_SPACING_PCT = 0.5
const TP_PCT_SINGLE = 0.5          // back to v1 original
const TP_PCT_DCA = 0.25            // back to v1 original
const SL_FROM_WORST_PCT = 0.6
const TREND_GAP_MIN_PCT = 0.25     // back to v1 original
const ATR_MAX_PCT = 0.60
const WEEKEND_QTY_MULT = 2.0       // back to v1 original
const DAILY_MAX_LOSS = 200          // back to v1 original
const TIME_SL_HOURS = 6            // v1.1's 6h time-SL (SMART variant: only on loss)
const BREAKER_PAUSE_MIN = 15
const COMMISSION_PCT = 0.0004
const SLIPPAGE_BPS = 2

// RSI threshold changed from 30/70 to 33/67 — change in getEntries() call below

console.log('═══ STRICT v1 / v1.1 backtest with exact v1 exit logic ═══')
console.log(`Period: ${TEST_DAYS}d | Capital: $${INITIAL_BALANCE} | Leverage: ${LEVERAGE}×`)
console.log(`Adaptive TP: ${TP_PCT_SINGLE}% (L1-only) / ${TP_PCT_DCA}% (post-DCA)`)
console.log(`SL: ${SL_FROM_WORST_PCT}% from WORST entry`)
console.log(`Trend-flip exit: ON (15m EMA20 vs EMA50 cross)`)
console.log(`Weekend ${WEEKEND_QTY_MULT}× | Daily stop $${DAILY_MAX_LOSS}\n`)

// ═══════════════════════════ LOAD DATA ═══════════════════════════
console.log('1. Loading bars + computing indicators…')
const lines = fs.readFileSync(CSV_PATH, 'utf-8').trim().split('\n')
const allBars: any[] = []
for (let i = 1; i < lines.length; i++) {
  const c = lines[i].split(',')
  allBars.push({
    time: Math.floor(new Date(c[0]).getTime() / 1000) * 1000,
    open: +c[1], high: +c[2], low: +c[3], close: +c[4], volume: +c[5],
    symbol: 'BTCUSDT', isFinal: true,
  })
}
const cutoff = allBars[allBars.length - 1].time - TEST_DAYS * 86400 * 1000
const data5m = allBars.filter(b => b.time >= cutoff)

function agg(intervalMs: number, src: any[]) {
  const out: any[] = []; let bk: any = null
  for (const b of src) {
    const t = Math.floor(b.time / intervalMs) * intervalMs
    if (!bk || bk.time !== t) { if (bk) out.push(bk); bk = { time: t, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume, symbol: 'BTCUSDT', isFinal: true } }
    else { bk.high = Math.max(bk.high, b.high); bk.low = Math.min(bk.low, b.low); bk.close = b.close; bk.volume += b.volume }
  }
  if (bk) out.push(bk); return out
}
const data15m = agg(15 * 60 * 1000, data5m)
console.log(`   ${data5m.length} × 5m bars, ${data15m.length} × 15m bars`)

// Pre-compute ATR(14) on 5m
const atr5m = new Float64Array(data5m.length)
let prevClose = data5m[0].close, trSum = 0
for (let i = 0; i < data5m.length; i++) {
  const b = data5m[i]
  const tr = Math.max(b.high - b.low, Math.abs(b.high - prevClose), Math.abs(b.low - prevClose))
  if (i < 14) { trSum += tr; atr5m[i] = i === 13 ? trSum / 14 : 0 }
  else atr5m[i] = (atr5m[i-1] * 13 + tr) / 14
  prevClose = b.close
}

// Pre-compute EMA20 + EMA50 on 15m for trend gate + trend-flip exit
function ema(values: number[], period: number) {
  const k = 2 / (period + 1); const out = new Array(values.length).fill(0); out[0] = values[0]
  for (let i = 1; i < values.length; i++) out[i] = values[i] * k + out[i-1] * (1 - k)
  return out
}
const closes15m = data15m.map((b: any) => b.close)
const ema20_15m = ema(closes15m, 20)
const ema50_15m = ema(closes15m, 50)

const ema15mByTime = new Map<number, { ema20: number; ema50: number; trend: 'UP'|'DOWN' }>()
for (let i = 0; i < data15m.length; i++) {
  ema15mByTime.set(data15m[i].time, {
    ema20: ema20_15m[i], ema50: ema50_15m[i],
    trend: ema20_15m[i] > ema50_15m[i] ? 'UP' : 'DOWN',
  })
}
function get15m(time: number) {
  return ema15mByTime.get(Math.floor(time / 900000) * 900000) ?? null
}

// Pre-compute custom entry filter (ATR + GAP)
const filterPass = new Set<number>()
let atrFails = 0, gapFails = 0
for (let i = 0; i < data5m.length; i++) {
  const b = data5m[i]
  if (atr5m[i] === 0) continue
  if ((atr5m[i] / b.close) * 100 > ATR_MAX_PCT) { atrFails++; continue }
  const e = get15m(b.time)
  if (!e || e.ema50 === 0) continue
  if (Math.abs((e.ema20 - e.ema50) / e.ema50) * 100 < TREND_GAP_MIN_PCT) { gapFails++; continue }
  filterPass.add(b.time)
}
console.log(`   Filter rejects: ATR ${atrFails} bars, GAP ${gapFails} bars`)
console.log(`   Entry-allowed bars: ${filterPass.size} (${(filterPass.size/data5m.length*100).toFixed(1)}%)\n`)

// ═════════════════════════ GAINIUM ENTRY DETECTION ═════════════════════════
const SYMBOL: any = {
  pair: 'BTCUSDT', exchange: ExchangeEnum.bybitUsdm,
  baseAsset: { minAmount: 0.001, maxAmount: 1500, step: 0.001, name: 'BTC' },
  quoteAsset: { minAmount: 5, name: 'USDT' }, maxOrders: 200, priceAssetPrecision: 1,
}
const PER_LEG_MARGIN = (INITIAL_BALANCE * 0.95 * LEVERAGE) / LEVERAGE / DCA_LEVELS

const RSI_THRESH = 30  // back to v1 original — SHORT mirrored to 70
async function getEntries(direction: 'LONG' | 'SHORT'): Promise<any[]> {
  const gid = uuidv4()
  const longSide = direction === 'LONG'
  // FIX 2026-06-07: Gainium defaults counBack=10000 which truncates 5m data to ~34 days.
  // Pass explicit `from` and `to` to use the full data range we provide.
  const fromMs = data5m[0].time
  const toMs = data5m[data5m.length - 1].time
  const bt: any = new DCABacktesting({
    exchange: ExchangeEnum.bybitUsdm,
    symbols: [{ ...SYMBOL }],
    interval: ExchangeIntervals.fiveM,
    balances: [{ asset: 'USDT', free: String(INITIAL_BALANCE), locked: '0' }],
    userFee: COMMISSION_PCT, slippage: SLIPPAGE_BPS / 10000,
    prices: [{ symbol: 'BTCUSDT', price: data5m[data5m.length - 1].close }],
    from: fromMs,
    to: toMs,
    settings: {
      pair: ['BTCUSDT'], name: `entry-only-${direction}`,
      strategy: longSide ? StrategyEnum.long : StrategyEnum.short,
      baseOrderSize: String(PER_LEG_MARGIN.toFixed(0)),
      orderSize: String(PER_LEG_MARGIN.toFixed(0)),
      orderSizeType: OrderSizeTypeEnum.usd,
      startOrderType: OrderTypeEnum.market, useLimitPrice: false,
      startCondition: StartConditionEnum.ti,
      useDca: false,      // we re-do DCA ourselves
      ordersCount: '1', activeOrdersCount: '1',
      step: '0.5', stepScale: '1', volumeScale: '1',
      useTp: true, tpPerc: '50',   // crazy high so Gainium never closes
      dealCloseCondition: CloseConditionEnum.tp,
      useSl: false,                  // disable Gainium SL entirely
      useSmartOrders: false, hodlDay: '', hodlAt: '', hodlNextBuy: 0,
      futures: true, leverage: LEVERAGE,
      maxNumberOfOpenDeals: '999',   // get every signal
      profitCurrency: 'quote', orderFixedIn: 'quote',
      cooldownAfterDealStart: false, cooldownAfterDealStop: false,
      indicators: [
        { uuid: uuidv4(), type: IndicatorEnum.rsi, indicatorLength: 9,
          indicatorValue: longSide ? String(RSI_THRESH) : String(100 - RSI_THRESH),
          indicatorCondition: longSide ? IndicatorStartConditionEnum.lt : IndicatorStartConditionEnum.gt,
          indicatorInterval: ExchangeIntervals.fiveM,
          indicatorAction: IndicatorAction.startDeal, groupId: gid },
        { uuid: uuidv4(), type: IndicatorEnum.ma, maType: MAEnum.ema,
          indicatorLength: 20, indicatorValue: 'crossing',
          maCrossingValue: MAEnum.ema, maCrossingLength: 50,
          maCrossingInterval: ExchangeIntervals.fifteenM, maUUID: uuidv4(),
          indicatorCondition: longSide ? IndicatorStartConditionEnum.gt : IndicatorStartConditionEnum.lt,
          indicatorInterval: ExchangeIntervals.fifteenM,
          indicatorAction: IndicatorAction.startDeal, groupId: gid },
      ],
      indicatorGroups: [{ id: gid, logic: IndicatorsLogicEnum.and, action: IndicatorAction.startDeal, section: IndicatorSection.controller }],
    },
    fullResult: true,
  } as any)
  const result = await bt.test([
    { interval: ExchangeIntervals.fiveM, bar: data5m },
    { interval: ExchangeIntervals.fifteenM, bar: data15m },
  ] as any)
  return ((result?.deals as any[]) ?? []).map(d => ({
    side: direction,
    startTime: d.startTime,
    startPrice: d.filledOrders?.[0]?.price ?? d.startPrice,
  }))
}

// ═════════════════════════ STRICT v1 SIMULATOR ═════════════════════════
// Replay v1 logic from entry: adaptive TP, SL from worst, BE-after-DCA, trend-flip exit
interface SimResult { entry: number; exit: number; reason: string; pnlPctOnEquity: number; weekend: boolean; legs: number }

function findBarIdx(time: number): number {
  // binary search
  let lo = 0, hi = data5m.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (data5m[mid].time < time) lo = mid + 1; else hi = mid
  }
  return lo
}

function simulateDeal(entryTime: number, entryPrice: number, side: 'LONG'|'SHORT', withTimeSL: boolean): SimResult | null {
  const startIdx = findBarIdx(entryTime)
  if (startIdx >= data5m.length - 1) return null
  const sign = side === 'LONG' ? 1 : -1

  // Position state
  const l1Price = entryPrice
  let worstPrice = l1Price       // worst = furthest adverse
  let avgPrice = l1Price
  let legs = 1                    // L1 filled
  let dcaPrice = side === 'LONG'
    ? l1Price * (1 - DCA_SPACING_PCT / 100)
    : l1Price * (1 + DCA_SPACING_PCT / 100)

  // Per-leg qty (gross): notional per leg / price
  const perLegNotional = PER_LEG_MARGIN * LEVERAGE
  const perLegQty = perLegNotional / l1Price
  // Weekend boost
  const dow = new Date(entryTime).getUTCDay()
  const weekend = (dow === 0 || dow === 6)
  const qtyMult = weekend ? WEEKEND_QTY_MULT : 1.0
  let totalQty = perLegQty * qtyMult

  // Entry trend (for trend-flip exit)
  const entryTrend = get15m(entryTime)?.trend

  // Walk bars after entry
  const maxBars = withTimeSL ? Math.ceil(TIME_SL_HOURS * 60 / 5) : data5m.length
  for (let i = startIdx + 1; i < data5m.length; i++) {
    const bar = data5m[i]
    const barsHeld = i - startIdx

    // ── 1. Check DCA L2 fill ──
    if (legs === 1) {
      const dcaHit = side === 'LONG' ? (bar.low <= dcaPrice) : (bar.high >= dcaPrice)
      if (dcaHit) {
        legs = 2
        worstPrice = dcaPrice
        avgPrice = (l1Price + dcaPrice) / 2
        // Add L2 qty (weekend multiplier applies to BOTH legs)
        const l2Qty = (perLegNotional / dcaPrice) * qtyMult
        totalQty += l2Qty
      }
    }

    // ── 2. Time-SL (v1.1) — ONLY fires if position is in LOSS at 6h ──
    // Logic: don't kill winners early. Only cut stuck losers.
    if (withTimeSL && barsHeld >= maxBars) {
      const exitPx = bar.close
      const grossPnl = (exitPx - avgPrice) * totalQty * sign
      const fees = exitPx * totalQty * COMMISSION_PCT
      const netPnl = grossPnl - fees
      // Smart time-SL: only exit if losing. If profitable, let it ride.
      if (netPnl < 0) {
        return { entry: entryTime, exit: bar.time, reason: 'TIME_SL',
                 pnlPctOnEquity: netPnl / INITIAL_BALANCE * 100,
                 weekend, legs }
      }
      // If in profit at 6h, continue holding. (Don't extend maxBars — let TP/SL handle from here.)
    }

    // ── 3. TP (adaptive: 0.5% L1-only, 0.25% post-DCA from avg) ──
    const tpPct = legs === 2 ? TP_PCT_DCA : TP_PCT_SINGLE
    const tpPrice = side === 'LONG'
      ? avgPrice * (1 + tpPct / 100)
      : avgPrice * (1 - tpPct / 100)
    const tpHit = side === 'LONG' ? (bar.high >= tpPrice) : (bar.low <= tpPrice)
    if (tpHit) {
      const grossPnl = (tpPrice - avgPrice) * totalQty * sign
      const fees = tpPrice * totalQty * COMMISSION_PCT
      const netPnl = grossPnl - fees
      return { entry: entryTime, exit: bar.time, reason: 'TP',
               pnlPctOnEquity: netPnl / INITIAL_BALANCE * 100,
               weekend, legs }
    }

    // ── 4. SL ──
    // v1 logic: SL is at SL_FROM_WORST below worst entry (BE-after-DCA if L2 filled)
    let slPrice: number
    if (legs === 2) {
      // BE-after-DCA: SL = avg entry
      slPrice = avgPrice
    } else {
      slPrice = side === 'LONG'
        ? worstPrice * (1 - SL_FROM_WORST_PCT / 100)
        : worstPrice * (1 + SL_FROM_WORST_PCT / 100)
    }
    const slHit = side === 'LONG' ? (bar.low <= slPrice) : (bar.high >= slPrice)
    if (slHit) {
      const exitPx = slPrice
      const grossPnl = (exitPx - avgPrice) * totalQty * sign
      const fees = exitPx * totalQty * COMMISSION_PCT
      const netPnl = grossPnl - fees
      return { entry: entryTime, exit: bar.time, reason: legs === 2 ? 'BE-DCA' : 'SL',
               pnlPctOnEquity: netPnl / INITIAL_BALANCE * 100,
               weekend, legs }
    }

    // ── 5. Trend-flip exit (15m EMA20/50 reverses) ──
    const trendNow = get15m(bar.time)?.trend
    if (entryTrend && trendNow && trendNow !== entryTrend) {
      const exitPx = bar.close
      const grossPnl = (exitPx - avgPrice) * totalQty * sign
      const fees = exitPx * totalQty * COMMISSION_PCT
      const netPnl = grossPnl - fees
      return { entry: entryTime, exit: bar.time, reason: 'TREND_FLIP',
               pnlPctOnEquity: netPnl / INITIAL_BALANCE * 100,
               weekend, legs }
    }
  }

  // Hit end of data — close at last bar
  const exitPx = data5m[data5m.length - 1].close
  const grossPnl = (exitPx - avgPrice) * totalQty * sign
  const fees = exitPx * totalQty * COMMISSION_PCT
  const netPnl = grossPnl - fees
  return { entry: entryTime, exit: data5m[data5m.length-1].time, reason: 'END_OF_DATA',
           pnlPctOnEquity: netPnl / INITIAL_BALANCE * 100,
           weekend, legs }
}

// ═════════════════════════ MAIN ═════════════════════════
async function runStrict(label: string, withTimeSL: boolean) {
  console.log(`\n╔══════════════════════════════════════════════════════════════╗`)
  console.log(`║  ${label}${withTimeSL ? ' (+ 12h smart time-SL)' : ' (no time-SL)'}`.padEnd(63) + `║`)
  console.log(`╚══════════════════════════════════════════════════════════════╝`)
  const t0 = Date.now()
  console.log(`   [${new Date().toISOString().slice(11,19)}] Detecting LONG entries...`)
  const longEntries  = await getEntries('LONG')
  console.log(`   [${new Date().toISOString().slice(11,19)}] LONG entries: ${longEntries.length} (${((Date.now()-t0)/1000).toFixed(1)}s)`)
  console.log(`   [${new Date().toISOString().slice(11,19)}] Detecting SHORT entries...`)
  const shortEntries = await getEntries('SHORT')
  console.log(`   [${new Date().toISOString().slice(11,19)}] SHORT entries: ${shortEntries.length} (total ${((Date.now()-t0)/1000).toFixed(1)}s)`)
  console.log(`   [${new Date().toISOString().slice(11,19)}] Simulating ${longEntries.length + shortEntries.length} raw entries...`)
  const allEntries = [...longEntries, ...shortEntries].sort((a, b) => a.startTime - b.startTime)

  let balance = INITIAL_BALANCE, peak = balance, maxDD = 0
  const dailyLoss = new Map<string, number>()
  let openUntil = 0, lossCooldownUntil = 0
  let nKept = 0, nDropFilter = 0, nDropPosition = 0, nDropDaily = 0, nDropCooldown = 0
  let wins = 0, losses = 0
  let totalWinUsd = 0, totalLossUsd = 0
  let biggestWin = 0, biggestLoss = 0
  let consecutiveWins = 0, consecutiveLosses = 0
  let maxConsecWins = 0, maxConsecLosses = 0
  let weekendWins = 0, weekendLosses = 0, weekendProfit = 0
  let dcaTrades = 0, l1OnlyTrades = 0
  let totalHoldMinutes = 0
  let reasonCount = { TP: 0, 'BE-DCA': 0, TREND_FLIP: 0, TIME_SL: 0, END_OF_DATA: 0 } as any
  let reasonProfit = { TP: 0, 'BE-DCA': 0, TREND_FLIP: 0, TIME_SL: 0, END_OF_DATA: 0 } as any
  let reasonWins = { TP: 0, 'BE-DCA': 0, TREND_FLIP: 0, TIME_SL: 0, END_OF_DATA: 0 } as any
  let reasonLosses = { TP: 0, 'BE-DCA': 0, TREND_FLIP: 0, TIME_SL: 0, END_OF_DATA: 0 } as any
  const equityCurve: { time: number; balance: number; pnl: number }[] = [{ time: allEntries[0]?.startTime ?? 0, balance, pnl: 0 }]

  for (const e of allEntries) {
    if (e.startTime < openUntil) { nDropPosition++; continue }
    if (e.startTime < lossCooldownUntil) { nDropCooldown++; continue }
    if (!filterPass.has(e.startTime)) { nDropFilter++; continue }
    const day = new Date(e.startTime).toISOString().slice(0, 10)
    const dl = dailyLoss.get(day) ?? 0
    if (dl <= -DAILY_MAX_LOSS) { nDropDaily++; continue }
    nKept++
    const sim = simulateDeal(e.startTime, e.startPrice, e.side, withTimeSL)
    if (!sim) continue

    // ── COMPOUNDING ── scale dollar P&L by current/initial balance ratio
    // sim.pnlPctOnEquity = % of INITIAL equity; compound scales it by current balance
    const scaled = sim.pnlPctOnEquity * (balance / INITIAL_BALANCE) / 100 * INITIAL_BALANCE
    balance += scaled
    if (balance > peak) peak = balance
    const dd = (peak - balance) / peak * 100; if (dd > maxDD) maxDD = dd
    if (scaled > 0) {
      wins++; totalWinUsd += scaled
      if (scaled > biggestWin) biggestWin = scaled
      consecutiveWins++; consecutiveLosses = 0
      if (consecutiveWins > maxConsecWins) maxConsecWins = consecutiveWins
    } else if (scaled < 0) {
      losses++; totalLossUsd += scaled
      if (scaled < biggestLoss) biggestLoss = scaled
      consecutiveLosses++; consecutiveWins = 0
      if (consecutiveLosses > maxConsecLosses) maxConsecLosses = consecutiveLosses
      lossCooldownUntil = sim.exit + BREAKER_PAUSE_MIN * 60 * 1000
    }
    if (sim.weekend) {
      if (scaled > 0) weekendWins++; else if (scaled < 0) weekendLosses++
      weekendProfit += scaled
    }
    if (sim.legs >= 2) dcaTrades++; else l1OnlyTrades++
    totalHoldMinutes += (sim.exit - sim.entry) / 60000
    dailyLoss.set(day, dl + Math.min(0, scaled))
    reasonCount[sim.reason]++
    reasonProfit[sim.reason] += scaled
    if (scaled > 0) reasonWins[sim.reason]++
    else if (scaled < 0) reasonLosses[sim.reason]++
    openUntil = sim.exit
    equityCurve.push({ time: sim.exit, balance, pnl: scaled })
  }

  const profit = balance - INITIAL_BALANCE
  const ret = profit / INITIAL_BALANCE * 100
  const wr = (wins + losses) > 0 ? wins / (wins + losses) * 100 : 0
  const avgWin = wins > 0 ? totalWinUsd / wins : 0
  const avgLoss = losses > 0 ? totalLossUsd / losses : 0
  const profitFactor = totalLossUsd < 0 ? Math.abs(totalWinUsd / totalLossUsd) : Infinity
  const expectancy = (wr / 100) * avgWin + ((1 - wr / 100) * avgLoss)
  const avgHoldH = totalHoldMinutes / (wins + losses) / 60
  const tradesPerDay = (wins + losses) / TEST_DAYS
  const annLinear = ret * (365 / TEST_DAYS)
  const annCompound = (Math.pow(balance / INITIAL_BALANCE, 365 / TEST_DAYS) - 1) * 100

  console.log()
  console.log(`📊 OVERVIEW`)
  console.log(`   Raw entries from Gainium:    ${allEntries.length} (${longEntries.length}L / ${shortEntries.length}S)`)
  console.log(`   Filtered out by ATR/GAP:     ${nDropFilter}`)
  console.log(`   Blocked (already in pos):    ${nDropPosition}`)
  console.log(`   Blocked (daily $200 stop):   ${nDropDaily}`)
  console.log(`   Blocked (post-loss cooldn):  ${nDropCooldown}`)
  console.log(`   → ACTIVE TRADES:             ${nKept}`)
  console.log()
  console.log(`💰 P&L (COMPOUNDED)`)
  console.log(`   Starting balance:            $${INITIAL_BALANCE.toFixed(2)}`)
  console.log(`   Ending balance:              $${balance.toFixed(2)}`)
  console.log(`   Net profit:                  $${profit.toFixed(2)} (${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%)`)
  console.log(`   Peak balance:                $${peak.toFixed(2)}`)
  console.log(`   Max drawdown:                ${maxDD.toFixed(2)}%`)
  console.log()
  console.log(`📈 TRADE STATS`)
  console.log(`   Total closed:                ${wins + losses}`)
  console.log(`   Wins / Losses:               ${wins} / ${losses}`)
  console.log(`   Win rate:                    ${wr.toFixed(1)}%`)
  console.log(`   Avg win:                     $${avgWin.toFixed(2)}`)
  console.log(`   Avg loss:                    $${avgLoss.toFixed(2)}`)
  console.log(`   Biggest win:                 $${biggestWin.toFixed(2)}`)
  console.log(`   Biggest loss:                $${biggestLoss.toFixed(2)}`)
  console.log(`   Max consec wins:             ${maxConsecWins}`)
  console.log(`   Max consec losses:           ${maxConsecLosses}`)
  console.log(`   Profit factor:               ${profitFactor.toFixed(2)}`)
  console.log(`   Expectancy per trade:        $${expectancy.toFixed(2)}`)
  console.log(`   Avg hold time:               ${avgHoldH.toFixed(1)} hours`)
  console.log(`   Trades per day:              ${tradesPerDay.toFixed(2)}`)
  console.log()
  console.log(`🔚 EXIT REASONS — break down by P&L`)
  console.log(`   Reason          Count | Wins | Losses | TotalP&L | Avg/trade`)
  console.log(`   ────────────────────────────────────────────────────────────`)
  for (const [k, v] of Object.entries(reasonCount).filter(([_, v]: any) => v > 0)) {
    const n = v as number
    const total = reasonProfit[k]
    const w = reasonWins[k], l = reasonLosses[k]
    const avg = total / n
    console.log(`   ${k.padEnd(15)} ${String(n).padStart(3)} | ${String(w).padStart(3)}  | ${String(l).padStart(3)}    | $${total.toFixed(2).padStart(7)} | $${avg.toFixed(2).padStart(7)}`)
  }
  console.log()
  console.log(`🎯 POSITION TYPES`)
  console.log(`   L1-only exits:               ${l1OnlyTrades} (${(l1OnlyTrades/(l1OnlyTrades+dcaTrades)*100).toFixed(0)}%)`)
  console.log(`   DCA hit:                     ${dcaTrades} (${(dcaTrades/(l1OnlyTrades+dcaTrades)*100).toFixed(0)}%)`)
  console.log()
  console.log(`📅 WEEKEND PERFORMANCE`)
  console.log(`   Weekend wins:                ${weekendWins}`)
  console.log(`   Weekend losses:              ${weekendLosses}`)
  console.log(`   Weekend net P&L:             $${weekendProfit.toFixed(2)}`)
  console.log()
  console.log(`📆 ANNUALIZED PROJECTION`)
  console.log(`   Linear (no compounding):     ${annLinear.toFixed(0)}% per year`)
  console.log(`   Compounded (recurring):      ${annCompound.toFixed(0)}% per year`)
  return { profit, ret, wins, losses, balance, maxDD }
}

(async () => {
  console.log('2. Getting raw entries from Gainium…')
  await runStrict('v1', false)
  await runStrict('v1.1', true)
})()
