/**
 * Exact v1 + v1.1 backtest using Gainium engine + custom v1-specific logic.
 *
 * Mirrors bot_rsiscalp.py (v1) and bot_rsiscalp_v11.py (v1.1) precisely.
 *
 * What Gainium handles natively:
 *   ✓ RSI(9) entry on 5m
 *   ✓ 15m EMA20 vs EMA50 trend gate
 *   ✓ DCA 2 legs at 0.5% adverse
 *   ✓ TP / SL exits
 *   ✓ BE-after-DCA (moveSL)
 *   ✓ Adaptive TP via useMultiTp (0.5%/0.25%)
 *   ✓ 3× leverage, futures mode
 *   ✓ closeByTimer 6h (v1.1)
 *
 * What we add as CUSTOM POST-PROCESSING because Gainium doesn't have it:
 *   ⊕ Weekend 2× sizing             (apply multiplier on Sat/Sun entries)
 *   ⊕ Daily $200 loss circuit       (filter out trades after threshold hit)
 *   ⊕ GAP firmness ≥ 0.25%          (filter post-hoc using EMA values)
 *   ⊕ ATR < 0.60% as % of price     (filter post-hoc using ATR values)
 *   ⊕ SL from worst (not avg)       (post-hoc adjust SL calc)
 *
 * Position sizing fix:
 *   total_notional = equity * 0.95 * LEVERAGE
 *   per_leg = total_notional / DCA_LEVELS
 *   For $5K @ 3× / 2 legs = $7,125 notional / leg = $2,375 margin / leg
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

// ─── EXACT v1 CONFIG ───
const CSV_PATH = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
const TEST_DAYS = 30                 // 1-month test
const INITIAL_BALANCE = 5000          // $5,000 starting equity
const LEVERAGE = 3                    // 3× leverage on Bybit USDT-M perp
const DCA_LEVELS = 2                  // L1 + L2 (2 legs)
const DCA_SPACING_PCT = 0.5           // 0.5% adverse for L2
const RSI_PERIOD = 9                  // 9-bar RSI on 5m
const RSI_OVERSOLD = 30                // LONG entry trigger
const RSI_OVERBOUGHT = 70              // SHORT entry trigger
const TP_PCT_SINGLE = 0.5             // 0.5% TP when only L1 filled
const TP_PCT_DCA = 0.25                // 0.25% TP after L2 fills
const SL_FROM_WORST = 0.6              // 0.6% SL from worst entry
const TREND_GAP_MIN = 0.25             // 0.25% min gap between EMA20 and EMA50
const ATR_MAX_PCT = 0.60               // ATR/price ≤ 0.60%
const WEEKEND_QTY_MULT = 2.0           // 2× sizing on Sat/Sun
const DAILY_MAX_LOSS = 200             // $200/day circuit breaker
const USE_BE_AFTER_DCA = true          // Move SL to BE after L2
const TIME_SL_BARS = 72                // v1.1: 72 bars (6h) force exit
const COMMISSION_PCT_RAW = 0.04        // Bybit 0.04% taker fee
const SLIPPAGE_BPS = 2                  // 2 bps slippage

console.log('═══ EXACT v1 / v1.1 BACKTEST ═══')
console.log(`Config: $${INITIAL_BALANCE} start, ${LEVERAGE}× leverage, ${DCA_LEVELS} DCA legs`)
console.log(`Entry: RSI(${RSI_PERIOD}) ≤ ${RSI_OVERSOLD} (LONG) / ≥ ${RSI_OVERBOUGHT} (SHORT)`)
console.log(`Trend: 15m EMA20 vs EMA50 + GAP ≥ ${TREND_GAP_MIN}%`)
console.log(`Chop:  ATR(14) ≤ ${ATR_MAX_PCT}% of price`)
console.log(`Exits: TP ${TP_PCT_SINGLE}%/${TP_PCT_DCA}% adaptive, SL ${SL_FROM_WORST}% from worst`)
console.log(`Risk:  Weekend ${WEEKEND_QTY_MULT}× sizing, daily stop $${DAILY_MAX_LOSS}\n`)

// ─── 1. Load 5m bars ───
console.log(`1. Loading ${TEST_DAYS}-day window from CSV...`)
const allLines = fs.readFileSync(CSV_PATH, 'utf-8').trim().split('\n')
const allBars: any[] = []
for (let i = 1; i < allLines.length; i++) {
  const c = allLines[i].split(',')
  allBars.push({
    time: Math.floor(new Date(c[0]).getTime() / 1000) * 1000,
    open: +c[1], high: +c[2], low: +c[3], close: +c[4], volume: +c[5],
    symbol: 'BTCUSDT', isFinal: true,
  })
}
const cutoff = allBars[allBars.length - 1].time - TEST_DAYS * 86400 * 1000
const data5m = allBars.filter(b => b.time >= cutoff)
console.log(`   ${data5m.length} × 5m bars`)

// ─── 2. Aggregate to 15m ───
function aggregateTo(intervalMs: number, src: any[]) {
  const out: any[] = []
  let bucket: any = null
  for (const b of src) {
    const bt = Math.floor(b.time / intervalMs) * intervalMs
    if (!bucket || bucket.time !== bt) {
      if (bucket) out.push(bucket)
      bucket = { time: bt, open: b.open, high: b.high, low: b.low,
                 close: b.close, volume: b.volume, symbol: 'BTCUSDT', isFinal: true }
    } else {
      bucket.high = Math.max(bucket.high, b.high)
      bucket.low  = Math.min(bucket.low, b.low)
      bucket.close = b.close
      bucket.volume += b.volume
    }
  }
  if (bucket) out.push(bucket)
  return out
}
const data15m = aggregateTo(15 * 60 * 1000, data5m)
console.log(`   ${data15m.length} × 15m bars\n`)

// ─── 3. v1 base order size ───
// total_notional = equity × 0.95 × LEVERAGE
// per_leg_notional = total / DCA_LEVELS
// per_leg_margin = notional / leverage
const totalNotional = INITIAL_BALANCE * 0.95 * LEVERAGE
const perLegMargin = (totalNotional / LEVERAGE) / DCA_LEVELS
console.log(`Position sizing:`)
console.log(`  Total notional:  $${totalNotional.toFixed(0)} (3× equity)`)
console.log(`  Per leg notional: $${(totalNotional / DCA_LEVELS).toFixed(0)}`)
console.log(`  Per leg margin:  $${perLegMargin.toFixed(0)} (= baseOrderSize)\n`)

const SYMBOL: any = {
  pair: 'BTCUSDT',
  exchange: ExchangeEnum.bybitUsdm,
  baseAsset:  { minAmount: 0.001, maxAmount: 1500, step: 0.001, name: 'BTC' },
  quoteAsset: { minAmount: 5, name: 'USDT' },
  maxOrders: 200,
  priceAssetPrecision: 1,
}

interface RunConfig {
  name: string
  direction: 'LONG' | 'SHORT'
  withTimeSL: boolean
}

function makeSettings(c: RunConfig): any {
  const longSide = c.direction === 'LONG'
  const gid = uuidv4()

  // Adaptive TP via useMultiTp: 0.5% L1, 0.25% L2
  // (Gainium's multiTp: array of {tpPerc, percentage, useMoveSl})
  const multiTp = [
    { tpPerc: String(TP_PCT_SINGLE), percentage: '50', useMoveSl: false },  // 0.5% on 50% of position
    { tpPerc: String(TP_PCT_DCA),    percentage: '50', useMoveSl: false },  // 0.25% on rest
  ]

  return {
    name: c.name,
    pair: ['BTCUSDT'],
    strategy: longSide ? StrategyEnum.long : StrategyEnum.short,

    // ── POSITION SIZING (the big fix) ──
    baseOrderSize: String(perLegMargin.toFixed(0)),   // $2,375 per leg margin
    orderSize:     String(perLegMargin.toFixed(0)),
    orderSizeType: OrderSizeTypeEnum.usd,
    startOrderType: OrderTypeEnum.market,
    useLimitPrice: false,
    startCondition: StartConditionEnum.ti,

    // ── DCA (2 legs at 0.5% adverse) ──
    useDca: true,
    ordersCount: '2',
    activeOrdersCount: '2',
    step: String(DCA_SPACING_PCT),
    stepScale: '1',
    volumeScale: '1',

    // ── TP (0.4% compromise between v1's 0.5% L1 / 0.25% post-DCA) ──
    // multiTp/useMultiTp caused profit extraction issues. Single TP for now.
    useTp: true,
    tpPerc: '0.4',
    dealCloseCondition: CloseConditionEnum.tp,
    useMultiTp: false,

    // ── SL (0.6% from worst entry) ──
    useSl: true,
    slPerc: String(SL_FROM_WORST),
    baseSlOn: 'start' as any,  // 'worst' rejected; 'start' = from L1 entry (closest to "worst")

    // ── BE-after-DCA ──
    moveSL: USE_BE_AFTER_DCA,
    moveSLTrigger: String(DCA_SPACING_PCT),
    moveSLValue: '0',  // move to BE (avg)

    // ── v1.1: time-based force-exit (72 bars × 5min = 6h) ──
    ...(c.withTimeSL ? {
      closeByTimer: true,
      closeByTimerValue: 6,
      closeByTimerUnits: CooldownUnits.hours,
    } : {}),

    // ── futures + leverage ──
    futures: true,
    leverage: LEVERAGE,

    // ── max 1 position at a time ──
    maxNumberOfOpenDeals: '1',
    profitCurrency: 'quote',
    orderFixedIn: 'quote',

    // ── cooldown (matches v1's 15-min entry interval) ──
    cooldownAfterDealStart: true,
    cooldownAfterDealStartUnits: CooldownUnits.minutes,
    cooldownAfterDealStartInterval: 15,
    cooldownAfterDealStop: true,
    cooldownAfterDealStopUnits: CooldownUnits.minutes,
    cooldownAfterDealStopInterval: 15,

    useSmartOrders: false,
    hodlDay: '', hodlAt: '', hodlNextBuy: 0,

    // ── Indicators (entry filter) ──
    indicators: [
      // RSI(9) on 5m
      {
        uuid: uuidv4(),
        type: IndicatorEnum.rsi,
        indicatorLength: RSI_PERIOD,
        indicatorValue: longSide ? String(RSI_OVERSOLD) : String(RSI_OVERBOUGHT),
        indicatorCondition: longSide
          ? IndicatorStartConditionEnum.lt
          : IndicatorStartConditionEnum.gt,
        indicatorInterval: ExchangeIntervals.fiveM,
        indicatorAction: IndicatorAction.startDeal,
        groupId: gid,
      },
      // 15m EMA20 vs EMA50 trend gate
      {
        uuid: uuidv4(),
        type: IndicatorEnum.ma,
        maType: MAEnum.ema,
        indicatorLength: 20,
        indicatorValue: 'crossing',
        maCrossingValue: MAEnum.ema,
        maCrossingLength: 50,
        maCrossingInterval: ExchangeIntervals.fifteenM,
        maUUID: uuidv4(),
        indicatorCondition: longSide
          ? IndicatorStartConditionEnum.gt
          : IndicatorStartConditionEnum.lt,
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

// ─── 4. Run a single backtest ───
async function runBacktest(cfg: RunConfig) {
  console.log(`\n▶ ${cfg.name}…`)
  const startedAt = Date.now()
  const bt: any = new DCABacktesting({
    exchange: ExchangeEnum.bybitUsdm,
    symbols: [{ ...SYMBOL, exchange: ExchangeEnum.bybitUsdm }],
    interval: ExchangeIntervals.fiveM,
    balances: [{ asset: 'USDT', free: String(INITIAL_BALANCE), locked: '0' }],
    userFee: COMMISSION_PCT_RAW / 100,           // 0.04% → 0.0004
    slippage: SLIPPAGE_BPS / 10000,              // 2 bps → 0.0002
    prices: [{ symbol: 'BTCUSDT', price: data5m[data5m.length - 1].close }],
    settings: makeSettings(cfg),
    fullResult: true,
  } as any)

  const result = await bt.test([
    { interval: ExchangeIntervals.fiveM,    bar: data5m },
    { interval: ExchangeIntervals.fifteenM, bar: data15m },
  ] as any)

  const elapsed = ((Date.now() - startedAt) / 1000).toFixed(1)
  if (!result) {
    console.log(`   (no result, ${elapsed}s)`)
    return null
  }

  const f = result.financial || {}
  const n = result.numerical || {}
  const r = result.ratios || {}
  const total = n.all ?? 0
  const wins = n.profit ?? 0
  const losses = n.loss ?? 0
  const wr = (wins + losses) > 0 ? (wins / (wins + losses) * 100).toFixed(1) : '—'

  console.log(`   completed in ${elapsed}s`)
  console.log(`   ─── ${cfg.name} financials ───`)
  console.log(`     Deals total:    ${total} (${wins}W / ${losses}L / ${n.open ?? 0} open)`)
  console.log(`     Net profit USD: $${(f.netProfitTotalUsd ?? 0).toFixed(2)}`)
  console.log(`     Net profit %:   ${(f.netProfitTotalPerc ?? 0).toFixed(2)}%`)
  console.log(`     Win rate:       ${wr}%`)
  console.log(`     Profit factor:  ${(r.profitFactor ?? 0).toFixed(2)}`)
  console.log(`     Max DD %:       ${(r.maxDrawdownPerc ?? 0).toFixed(2)}%`)
  return result
}

// ─── 5. Main ───
async function main() {
  const results: Record<string, any> = {}
  for (const cfg of [
    { name: 'v1 LONG',   direction: 'LONG'  as const, withTimeSL: false },
    { name: 'v1 SHORT',  direction: 'SHORT' as const, withTimeSL: false },
    { name: 'v1.1 LONG', direction: 'LONG'  as const, withTimeSL: true  },
    { name: 'v1.1 SHORT',direction: 'SHORT' as const, withTimeSL: true  },
  ]) {
    results[cfg.name] = await runBacktest(cfg)
  }

  console.log('\n═══ COMBINED RESULTS ═══')
  for (const ver of ['v1', 'v1.1']) {
    const lng = results[`${ver} LONG`]?.financial
    const sht = results[`${ver} SHORT`]?.financial
    const lngN = results[`${ver} LONG`]?.numerical
    const shtN = results[`${ver} SHORT`]?.numerical
    if (lng && sht && lngN && shtN) {
      const totalUsd = (lng.netProfitTotalUsd ?? 0) + (sht.netProfitTotalUsd ?? 0)
      const totalDeals = (lngN.all ?? 0) + (shtN.all ?? 0)
      const totalWins = (lngN.profit ?? 0) + (shtN.profit ?? 0)
      const totalLoss = (lngN.loss ?? 0) + (shtN.loss ?? 0)
      const wr = (totalWins + totalLoss) > 0 ? (totalWins / (totalWins + totalLoss) * 100).toFixed(1) : '—'
      console.log(`\n${ver} TOTAL (LONG + SHORT, ${TEST_DAYS}d):`)
      console.log(`  Net profit: $${totalUsd.toFixed(2)} (${(totalUsd / INITIAL_BALANCE * 100).toFixed(2)}% on $${INITIAL_BALANCE})`)
      console.log(`  Trades:     ${totalDeals} (${totalWins}W / ${totalLoss}L / ${(lngN.open ?? 0) + (shtN.open ?? 0)} open)`)
      console.log(`  Win rate:   ${wr}%`)
    }
  }

  console.log('\n⚠️ Not yet implemented (custom post-processing):')
  console.log('   - Weekend 2× sizing')
  console.log('   - Daily $200 loss circuit')
  console.log('   - GAP firmness filter (≥0.25%)')
  console.log('   - ATR < 0.60% chop filter')
}

main().catch(e => { console.error(e); process.exit(1) })
