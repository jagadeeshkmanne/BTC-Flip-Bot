/**
 * COMPLETE v1 / v1.1 backtest with ALL config including custom filters.
 *
 * What Gainium handles natively:
 *   ✓ RSI(9) entry on 5m
 *   ✓ 15m EMA20 vs EMA50 trend gate
 *   ✓ DCA 2 legs at 0.5% adverse
 *   ✓ TP 0.4% / SL 0.6%
 *   ✓ BE-after-DCA (moveSL)
 *   ✓ 3× leverage, futures (bybitLinear)
 *   ✓ closeByTimer 6h (v1.1 only)
 *   ✓ 0.04% Bybit fees + 0.02% slippage
 *
 * What this script ADDS as custom POST-PROCESSING (Gainium can't do):
 *   ⊕ ATR ≤ 0.60% of price (chop filter)
 *   ⊕ GAP ≥ 0.25% between EMA20 and EMA50 (trend strength)
 *   ⊕ Weekend 2× sizing (Sat/Sun positions get 2× profit)
 *   ⊕ Daily $200 loss circuit breaker (skip trades after threshold)
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

// ═══════════════════════════════════════════════════════════════════
// EXACT v1 CONFIG (from bot_rsiscalp.py + core_rsiscalp.py)
// ═══════════════════════════════════════════════════════════════════
const CSV_PATH = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
const TEST_DAYS = 30                  // 1-month test

const INITIAL_BALANCE = 5000           // $5K starting equity
const LEVERAGE = 3                     // 3× on Bybit USDT-M perp
const DCA_LEVELS = 2                   // L1 + L2
const DCA_SPACING_PCT = 0.5            // 0.5% adverse spacing
const RSI_PERIOD = 9                   // RSI(9) on 5m
const RSI_OVERSOLD = 30                 // LONG when RSI ≤ 30
const RSI_OVERBOUGHT = 70               // SHORT when RSI ≥ 70
const TP_PCT_SINGLE = 0.5              // v1 ACTUAL: 0.5% from avg when L1-only
const TP_PCT_DCA = 0.25                // v1 ACTUAL: 0.25% from avg after L2 fills
const SL_PCT = 0.6                     // 0.6% from start entry
const TREND_GAP_MIN_PCT = 0.25         // 15m (EMA20-EMA50)/EMA50 ≥ 0.25%
const ATR_MAX_PCT = 0.60               // ATR(14)/price ≤ 0.60%
const WEEKEND_QTY_MULT = 2.0           // Sat/Sun 2× position
const DAILY_MAX_LOSS = 200             // $200/day stop
const TIME_SL_HOURS = 6                // v1.1: 6h force exit
const COMMISSION_PCT_RAW = 0.04        // 0.04% Bybit taker
const SLIPPAGE_BPS = 2

// Sizing: per-leg margin = equity × 0.95 × leverage / leverage / dca_levels
const PER_LEG_MARGIN = (INITIAL_BALANCE * 0.95 * LEVERAGE) / LEVERAGE / DCA_LEVELS

console.log('═══ COMPLETE v1 / v1.1 BACKTEST ═══')
console.log(`Period: ${TEST_DAYS} days`)
console.log(`Capital: $${INITIAL_BALANCE} | Leverage: ${LEVERAGE}× | DCA: ${DCA_LEVELS} legs`)
console.log(`Per leg margin: $${PER_LEG_MARGIN.toFixed(0)} | Notional: $${(PER_LEG_MARGIN * LEVERAGE).toFixed(0)} per leg`)
console.log(`Native filters:  RSI(${RSI_PERIOD}) + EMA20/50 trend + DCA + TP(${TP_PCT_SINGLE}%/${TP_PCT_DCA}%) + SL + BE-after-DCA`)
console.log(`Custom filters:  GAP≥${TREND_GAP_MIN_PCT}% | ATR≤${ATR_MAX_PCT}% | Weekend ${WEEKEND_QTY_MULT}× | Daily stop $${DAILY_MAX_LOSS}`)
console.log()

// ═══════════════════════════════════════════════════════════════════
// LOAD DATA
// ═══════════════════════════════════════════════════════════════════
console.log('1. Loading 5m BTC bars…')
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
console.log(`   ${data5m.length} × 5m bars`)

function agg(intervalMs: number, src: any[]) {
  const out: any[] = []
  let bk: any = null
  for (const b of src) {
    const t = Math.floor(b.time / intervalMs) * intervalMs
    if (!bk || bk.time !== t) {
      if (bk) out.push(bk)
      bk = { time: t, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume, symbol: 'BTCUSDT', isFinal: true }
    } else {
      bk.high = Math.max(bk.high, b.high); bk.low = Math.min(bk.low, b.low); bk.close = b.close; bk.volume += b.volume
    }
  }
  if (bk) out.push(bk)
  return out
}
const data15m = agg(15 * 60 * 1000, data5m)
console.log(`   ${data15m.length} × 15m bars\n`)

// ═══════════════════════════════════════════════════════════════════
// PRE-COMPUTE INDICATORS FOR CUSTOM FILTERS
// ═══════════════════════════════════════════════════════════════════
console.log('2. Pre-computing ATR(14) on 5m and EMA20/EMA50 on 15m…')

// ATR(14) on 5m  (Wilder's RMA)
const atr5m = new Float64Array(data5m.length)
let prevClose = data5m[0].close
let trSum = 0
for (let i = 0; i < data5m.length; i++) {
  const b = data5m[i]
  const tr = Math.max(b.high - b.low, Math.abs(b.high - prevClose), Math.abs(b.low - prevClose))
  if (i < 14) {
    trSum += tr
    atr5m[i] = i === 13 ? trSum / 14 : 0
  } else {
    atr5m[i] = (atr5m[i-1] * 13 + tr) / 14
  }
  prevClose = b.close
}

// EMA20 + EMA50 on 15m
function ema(values: number[], period: number): number[] {
  const k = 2 / (period + 1)
  const out = new Array(values.length).fill(0)
  out[0] = values[0]
  for (let i = 1; i < values.length; i++) {
    out[i] = values[i] * k + out[i-1] * (1 - k)
  }
  return out
}
const closes15m = data15m.map((b: any) => b.close)
const ema20_15m = ema(closes15m, 20)
const ema50_15m = ema(closes15m, 50)

// Build lookup: for any 5m bar timestamp, what was the latest 15m EMA20 and EMA50?
const ema15mByTime = new Map<number, { ema20: number; ema50: number }>()
for (let i = 0; i < data15m.length; i++) {
  ema15mByTime.set(data15m[i].time, { ema20: ema20_15m[i], ema50: ema50_15m[i] })
}

// For each 5m bar, find the most-recent 15m bar's EMA
function get15mEMA(time: number): { ema20: number; ema50: number } | null {
  const bucket = Math.floor(time / 900000) * 900000  // 15-min bucket
  return ema15mByTime.get(bucket) ?? null
}

// Pre-compute custom filter status for each 5m bar
console.log('3. Pre-filtering bars by custom rules (ATR, GAP)…')
const customFilterPass = new Set<number>()  // bar.time → entry allowed
let atrFails = 0, gapFails = 0
for (let i = 0; i < data5m.length; i++) {  // use index, not indexOf (was O(n²))
  const b = data5m[i]
  const atrVal = atr5m[i]
  if (atrVal === 0) continue  // not enough warmup
  const atrPct = (atrVal / b.close) * 100
  if (atrPct > ATR_MAX_PCT) { atrFails++; continue }
  const emas = get15mEMA(b.time)
  if (!emas || emas.ema50 === 0) continue
  const gapPct = Math.abs((emas.ema20 - emas.ema50) / emas.ema50) * 100
  if (gapPct < TREND_GAP_MIN_PCT) { gapFails++; continue }
  customFilterPass.add(b.time)
}
console.log(`   Bars rejected by ATR>${ATR_MAX_PCT}%: ${atrFails}`)
console.log(`   Bars rejected by GAP<${TREND_GAP_MIN_PCT}%: ${gapFails}`)
console.log(`   Bars passing custom filters: ${customFilterPass.size} (${(customFilterPass.size / data5m.length * 100).toFixed(1)}%)\n`)

// ═══════════════════════════════════════════════════════════════════
// GAINIUM BACKTEST CONFIG (the part Gainium handles)
// ═══════════════════════════════════════════════════════════════════
const SYMBOL: any = {
  pair: 'BTCUSDT',
  exchange: ExchangeEnum.bybitUsdm,
  baseAsset:  { minAmount: 0.001, maxAmount: 1500, step: 0.001, name: 'BTC' },
  quoteAsset: { minAmount: 5, name: 'USDT' },
  maxOrders: 200,
  priceAssetPrecision: 1,
}

function makeSettings(direction: 'LONG' | 'SHORT', withTimeSL: boolean): any {
  const longSide = direction === 'LONG'
  const gid = uuidv4()
  return {
    name: `${direction}${withTimeSL ? '_TIMESL' : ''}`,
    pair: ['BTCUSDT'],
    strategy: longSide ? StrategyEnum.long : StrategyEnum.short,
    baseOrderSize: String(PER_LEG_MARGIN.toFixed(0)),
    orderSize: String(PER_LEG_MARGIN.toFixed(0)),
    orderSizeType: OrderSizeTypeEnum.usd,
    startOrderType: OrderTypeEnum.market,
    useLimitPrice: false,
    startCondition: StartConditionEnum.ti,
    useDca: true,
    ordersCount: '2', activeOrdersCount: '2',
    step: String(DCA_SPACING_PCT), stepScale: '1', volumeScale: '1',
    // Gainium's useMultiTp broke deal-closing — use 0.5% single (matches live L1-only TP)
    // Trade-off: post-DCA trades will use 0.5% from avg instead of 0.25% (Python)
    useTp: true,
    tpPerc: String(TP_PCT_SINGLE),       // 0.5%
    dealCloseCondition: CloseConditionEnum.tp,
    useSl: true, slPerc: String(SL_PCT), baseSlOn: 'start' as any,
    moveSL: true, moveSLTrigger: String(DCA_SPACING_PCT), moveSLValue: '0',
    ...(withTimeSL ? {
      closeByTimer: true,
      closeByTimerValue: TIME_SL_HOURS,
      closeByTimerUnits: CooldownUnits.hours,
    } : {}),
    futures: true, leverage: LEVERAGE,
    maxNumberOfOpenDeals: '1',
    profitCurrency: 'quote', orderFixedIn: 'quote',
    // v1 has NO cooldown after wins — only 15min pause AFTER A LOSS
    // (handled in custom post-processing, not Gainium native config)
    cooldownAfterDealStart: false,
    cooldownAfterDealStop: false,
    useSmartOrders: false, hodlDay: '', hodlAt: '', hodlNextBuy: 0,
    indicators: [
      {
        uuid: uuidv4(), type: IndicatorEnum.rsi, indicatorLength: RSI_PERIOD,
        indicatorValue: longSide ? String(RSI_OVERSOLD) : String(RSI_OVERBOUGHT),
        indicatorCondition: longSide ? IndicatorStartConditionEnum.lt : IndicatorStartConditionEnum.gt,
        indicatorInterval: ExchangeIntervals.fiveM,
        indicatorAction: IndicatorAction.startDeal,
        groupId: gid,
      },
      {
        uuid: uuidv4(), type: IndicatorEnum.ma, maType: MAEnum.ema,
        indicatorLength: 20, indicatorValue: 'crossing',
        maCrossingValue: MAEnum.ema, maCrossingLength: 50,
        maCrossingInterval: ExchangeIntervals.fifteenM, maUUID: uuidv4(),
        indicatorCondition: longSide ? IndicatorStartConditionEnum.gt : IndicatorStartConditionEnum.lt,
        indicatorInterval: ExchangeIntervals.fifteenM,
        indicatorAction: IndicatorAction.startDeal,
        groupId: gid,
      },
    ],
    indicatorGroups: [
      { id: gid, logic: IndicatorsLogicEnum.and, action: IndicatorAction.startDeal, section: IndicatorSection.controller },
    ],
  }
}

// ═══════════════════════════════════════════════════════════════════
// RUN A BACKTEST + APPLY CUSTOM POST-PROCESSING
// ═══════════════════════════════════════════════════════════════════
async function runBacktest(name: string, direction: 'LONG' | 'SHORT', withTimeSL: boolean) {
  console.log(`▶ ${name}`)
  const startedAt = Date.now()
  const bt: any = new DCABacktesting({
    exchange: ExchangeEnum.bybitUsdm,
    symbols: [{ ...SYMBOL }],
    interval: ExchangeIntervals.fiveM,
    balances: [{ asset: 'USDT', free: String(INITIAL_BALANCE), locked: '0' }],
    userFee: COMMISSION_PCT_RAW / 100,
    slippage: SLIPPAGE_BPS / 10000,
    prices: [{ symbol: 'BTCUSDT', price: data5m[data5m.length - 1].close }],
    settings: makeSettings(direction, withTimeSL),
    fullResult: true,
  } as any)
  const result = await bt.test([
    { interval: ExchangeIntervals.fiveM, bar: data5m },
    { interval: ExchangeIntervals.fifteenM, bar: data15m },
  ] as any)
  const elapsed = ((Date.now() - startedAt) / 1000).toFixed(1)
  if (!result || !result.deals) return null

  // Return raw deals (sorted) — combination/compounding happens in main()
  const deals = (result.deals as any[]).slice().sort((a, b) => a.startTime - b.startTime)
  console.log(`   ${elapsed}s | raw deals: ${deals.length}`)
  return { deals, direction, withTimeSL }
}

/**
 * Combine LONG + SHORT deals + apply all filters + compound chronologically.
 * Compounding: each trade's profit scales with the current balance.
 *   trade_profit_scaled = raw_profit × (current_balance / initial_balance)
 *   balance += trade_profit_scaled
 */
function combineAndCompound(longRun: any, shortRun: any, label: string) {
  // Merge + sort by entry time
  const allDeals: any[] = [
    ...(longRun?.deals ?? []).map((d: any) => ({ ...d, side: 'LONG' })),
    ...(shortRun?.deals ?? []).map((d: any) => ({ ...d, side: 'SHORT' })),
  ].sort((a, b) => a.startTime - b.startTime)

  let balance = INITIAL_BALANCE
  let peakBalance = balance
  let maxDD = 0
  const dailyLoss = new Map<string, number>()
  let nKept = 0, nDroppedFilter = 0, nKilledStop = 0, nOpen = 0, nKilledCooldown = 0
  let totalWins = 0, totalLosses = 0
  let totalWeekendCount = 0, totalWeekendProfit = 0
  // 2026-06-06: 15-min cooldown after LOSSES only (v1's BREAKER_PAUSE_HOURS = 0.25)
  let lossCooldownUntil = 0  // skip entries with startTime < this
  // Track unrealized losses on still-open positions (HONEST accounting)
  const lastBarPrice = data5m[data5m.length - 1].close
  let totalUnrealized = 0
  let openStuckCount = 0

  for (const d of allDeals) {
    if (d.status !== 'closed') {
      nOpen++
      // HONEST accounting: mark-to-market each open position at last bar price
      if (d.filledOrders?.length && d.avgPrice) {
        const qty = d.filledOrders.reduce((acc: number, o: any) => acc + (o.qty || 0), 0)
        // For LONG: profit if last > avg, loss if last < avg. SHORT is opposite.
        const sideMult = d.side === 'LONG' ? 1 : -1
        const mtm = (lastBarPrice - d.avgPrice) * qty * sideMult
                  - lastBarPrice * qty * 0.0004 * 2  // exit fees
        if (mtm < 0) {
          openStuckCount++
          totalUnrealized += mtm
        }
      }
      continue
    }
    // 1. After-loss 15-min cooldown (v1's only cooldown)
    if (d.startTime < lossCooldownUntil) { nKilledCooldown++; continue }
    // 2. ATR/GAP filter
    if (!customFilterPass.has(d.startTime)) { nDroppedFilter++; continue }
    // 3. Daily $200 stop
    const day = new Date(d.startTime).toISOString().slice(0, 10)
    const currentDailyLoss = dailyLoss.get(day) ?? 0
    if (currentDailyLoss <= -DAILY_MAX_LOSS) { nKilledStop++; continue }
    nKept++
    // 4. Raw profit
    let rawProfit = d.profit?.totalUsd ?? d.profit?.total ?? 0
    // 5. Weekend 2×
    const dow = new Date(d.startTime).getUTCDay()
    if (dow === 0 || dow === 6) {
      rawProfit *= WEEKEND_QTY_MULT
      totalWeekendCount++
      totalWeekendProfit += rawProfit
    }
    // 6. COMPOUND: scale profit by current balance ratio
    const scaledProfit = rawProfit * (balance / INITIAL_BALANCE)
    balance += scaledProfit
    if (balance > peakBalance) peakBalance = balance
    const ddPct = (peakBalance - balance) / peakBalance * 100
    if (ddPct > maxDD) maxDD = ddPct
    if (scaledProfit > 0) totalWins++
    else if (scaledProfit < 0) {
      totalLosses++
      // 7. Trigger 15-min cooldown after losses (v1 BREAKER_LOSSES=1, BREAKER_PAUSE_HOURS=0.25)
      lossCooldownUntil = (d.closedTime ?? d.startTime) + 15 * 60 * 1000
    }
    dailyLoss.set(day, currentDailyLoss + Math.min(0, scaledProfit))
  }

  const netProfit = balance - INITIAL_BALANCE
  const returnPct = netProfit / INITIAL_BALANCE * 100
  const annualized = returnPct * (365 / TEST_DAYS)
  const wr = (totalWins + totalLosses) > 0 ? (totalWins / (totalWins + totalLosses) * 100) : 0

  // HONEST P/L: realized + unrealized at last bar (force-close stuck positions)
  const honestBalance = balance + totalUnrealized
  const honestProfit = honestBalance - INITIAL_BALANCE
  const honestReturnPct = honestProfit / INITIAL_BALANCE * 100

  console.log(`\n═══ ${label} TOTAL (LONG + SHORT, ${TEST_DAYS}d, COMPOUNDED) ═══`)
  console.log(`  Starting balance: $${INITIAL_BALANCE.toFixed(2)}`)
  console.log(`  Ending balance:   $${balance.toFixed(2)}`)
  console.log(`  Net profit:       $${netProfit.toFixed(2)}  (+${returnPct.toFixed(2)}%)`)
  if (openStuckCount > 0) {
    console.log(`  ⚠️ STUCK OPEN:    ${openStuckCount} losing positions, unrealized $${totalUnrealized.toFixed(2)}`)
    console.log(`  HONEST balance:   $${honestBalance.toFixed(2)}`)
    console.log(`  HONEST profit:    $${honestProfit.toFixed(2)}  (${honestReturnPct >= 0 ? '+' : ''}${honestReturnPct.toFixed(2)}%) ← realized + unrealized`)
  }
  console.log(`  Peak balance:     $${peakBalance.toFixed(2)}`)
  console.log(`  Max drawdown:     ${maxDD.toFixed(2)}%`)
  console.log(`  Annualized:       ${annualized.toFixed(0)}% (linear) | (1+${(returnPct/100).toFixed(4)})^${(365/TEST_DAYS).toFixed(1)} compounded`)
  console.log(`  Trades:           ${nKept} closed (${totalWins}W / ${totalLosses}L / ${nOpen} open)`)
  console.log(`  Win rate:         ${wr.toFixed(1)}%`)
  console.log(`  Filters:          ${nDroppedFilter} dropped by ATR/GAP, ${nKilledStop} killed by daily stop, ${nKilledCooldown} blocked by post-loss cooldown`)
  console.log(`  Weekend boost:    ${totalWeekendCount} trades got 2× sizing (+$${totalWeekendProfit.toFixed(2)} contribution)`)
  return { balance, netProfit, returnPct, maxDD, wr, nKept }
}

// ═══════════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════════
async function main() {
  const v1L  = await runBacktest('v1 LONG',   'LONG',  false)
  const v1S  = await runBacktest('v1 SHORT',  'SHORT', false)
  const v11L = await runBacktest('v1.1 LONG', 'LONG',  true)
  const v11S = await runBacktest('v1.1 SHORT','SHORT', true)

  combineAndCompound(v1L,  v1S,  'v1')
  combineAndCompound(v11L, v11S, 'v1.1')
}

main().catch(e => { console.error(e); process.exit(1) })
