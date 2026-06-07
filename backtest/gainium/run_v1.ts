/**
 * Backtest v1 + v1.1 strategies using Gainium's official backtester.
 *
 * v1 settings (from BTC-Flip-Bot/strategies/day/bot_rsiscalp.py):
 *   - 5m BTCUSDT perpetual, 3x leverage
 *   - Entry LONG:  RSI(9) ≤ 30 AND 15m EMA20 > EMA50 AND ATR < 0.6%
 *   - Entry SHORT: RSI(9) ≥ 70 AND 15m EMA20 < EMA50 AND ATR < 0.6%
 *   - DCA: 2 legs at 0.5% adverse, equal size
 *   - TP: 0.4% from avg (compromise between v1's 0.5% L1 / 0.25% post-DCA)
 *   - SL: 0.6% from worst entry
 *   - Move SL to break-even after L2 fires (BE-after-DCA)
 *
 * v1.1 adds: closeByTimer 6h force-exit
 *
 * Strategy is mutually exclusive by trend filter (LONG only in uptrend,
 * SHORT only in downtrend), so we run LONG + SHORT separately and sum.
 */

import DCABacktesting from './src/dca'
import {
  ExchangeIntervals, ExchangeEnum, StrategyEnum,
  OrderTypeEnum, StartConditionEnum, OrderSizeTypeEnum,
  IndicatorEnum, IndicatorStartConditionEnum, IndicatorsLogicEnum,
  IndicatorAction, IndicatorSection, MAEnum,
  CooldownUnits, CloseConditionEnum, CurrencyEnum,
} from './src/types'
import * as fs from 'fs'
import * as path from 'path'
import { v4 as uuidv4 } from 'uuid'

// ── Configuration ──
const CSV_PATH = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
const YEARS_TO_TEST_DAYS = 365  // 1 year
const STARTING_BALANCE = 5000

console.log('═══ Gainium-Backtester v1/v1.1 runner ═══\n')

// ─── 1. Load 5m candles ───
console.log('1. Loading BTC 5m candles…')
const lines = fs.readFileSync(CSV_PATH, 'utf-8').trim().split('\n')
const all5m: any[] = []
for (let i = 1; i < lines.length; i++) {
  const c = lines[i].split(',')
  all5m.push({
    time: Math.floor(new Date(c[0]).getTime() / 1000) * 1000, // ms epoch
    open: +c[1], high: +c[2], low: +c[3], close: +c[4], volume: +c[5],
    symbol: 'BTCUSDT', isFinal: true,
  })
}
const cutoff = all5m[all5m.length - 1].time - YEARS_TO_TEST_DAYS * 86400 * 1000
const data5m = all5m.filter(b => b.time >= cutoff)
console.log(`   ${data5m.length} bars (${YEARS_TO_TEST_DAYS}d window)`)
console.log(`   from ${new Date(data5m[0].time).toISOString().slice(0,10)} to ${new Date(data5m[data5m.length-1].time).toISOString().slice(0,10)}`)

// ─── 2. Aggregate to 15m for HTF indicators ───
console.log('2. Aggregating to 15m…')
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
console.log(`   ${data15m.length} 15m bars`)

// ─── 3. Build settings for v1 ───
const SYMBOL: any = {
  pair: 'BTCUSDT',
  exchange: ExchangeEnum.bybit,
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

  return {
    name: c.name,
    pair: ['BTCUSDT'],
    strategy: longSide ? StrategyEnum.long : StrategyEnum.short,
    baseOrderSize: '100',
    baseOrderPrice: '',
    orderSize: '100',
    orderSizeType: OrderSizeTypeEnum.usd,
    startOrderType: OrderTypeEnum.market,
    useLimitPrice: false,
    startCondition: StartConditionEnum.ti,   // Technical Indicators

    // DCA: 2 legs at 0.5% adverse, equal size
    useDca: true,
    ordersCount: '2',
    activeOrdersCount: '2',
    step: '0.5',
    stepScale: '1',
    volumeScale: '1',

    // TP & SL
    useTp: true,
    tpPerc: '0.4',
    dealCloseCondition: CloseConditionEnum.tp,
    useSl: true,
    slPerc: '0.6',
    baseSlOn: 'avg' as any,

    // BE-after-DCA
    moveSL: true,
    moveSLTrigger: '0.5',
    moveSLValue: '0',

    useSmartOrders: false,
    hodlDay: '', hodlAt: '', hodlNextBuy: 0,

    // 3× leverage on USDT-M perp (matches v1 Python bot)
    futures: true,
    leverage: 3,

    // v1.1: 6h time-based force-exit
    // Field is closeByTimerVALUE (not Interval) — easy bug to miss
    ...(c.withTimeSL ? {
      closeByTimer: true,
      closeByTimerValue: 6,
      closeByTimerUnits: CooldownUnits.hours,
    } : {}),

    maxNumberOfOpenDeals: '1',
    profitCurrency: 'quote' as CurrencyEnum,
    orderFixedIn: 'quote' as CurrencyEnum,

    cooldownAfterDealStart: true,
    cooldownAfterDealStartUnits: CooldownUnits.minutes,
    cooldownAfterDealStartInterval: 15,
    cooldownAfterDealStop: true,
    cooldownAfterDealStopUnits: CooldownUnits.minutes,
    cooldownAfterDealStopInterval: 15,

    // ── Entry indicators ──  (each needs indicatorAction = startDeal)
    indicators: [
      // RSI(9) on 5m: LONG ≤ 30, SHORT ≥ 70
      {
        uuid: uuidv4(),
        type: IndicatorEnum.rsi,
        indicatorLength: 9,
        indicatorValue: longSide ? '30' : '70',
        indicatorCondition: longSide
          ? IndicatorStartConditionEnum.lt
          : IndicatorStartConditionEnum.gt,
        indicatorInterval: ExchangeIntervals.fiveM,
        indicatorAction: IndicatorAction.startDeal,
        groupId: gid,
      },
      // 15m EMA20 vs EMA50: LONG → EMA20 > EMA50, SHORT → EMA20 < EMA50
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
      // ATR chop filter omitted — Gainium ATR returns absolute price value,
      // not percentage. Our Python uses atr/price*100 < 0.6. Skipping here
      // means slightly looser filter (more trades) but trend gate + RSI
      // do the heavy lifting.
    ],
    indicatorGroups: [
      {
        id: gid,
        logic: IndicatorsLogicEnum.and,
        action: IndicatorAction.startDeal,
        section: IndicatorSection.controller,
      },
    ],
  }
}

// ─── 4. Run a single backtest ───
async function runBacktest(cfg: RunConfig) {
  console.log(`\n4. Running ${cfg.name}…`)
  const startedAt = Date.now()
  const bt: any = new DCABacktesting({
    exchange: ExchangeEnum.bybitUsdm,   // bybitLinear — USDT-M perp
    symbols: [{ ...SYMBOL, exchange: ExchangeEnum.bybitUsdm }],
    interval: ExchangeIntervals.fiveM,
    balances: [{ asset: 'USDT', free: String(STARTING_BALANCE), locked: '0' }],
    userFee: 0.00055,          // Bybit taker 0.055% = 0.00055 (NOT 0.055!)
    slippage: 0.0002,          // 2 bps = 0.02% = 0.0002 (same trap)
    prices: [{ symbol: 'BTCUSDT', price: data5m[data5m.length - 1].close }],
    settings: makeSettings(cfg),
    fullResult: true,
    // combo: false  — leaving combo off: combo mode SKIPS filterTP entirely!
  } as any)

  const result = await bt.test([
    { interval: ExchangeIntervals.fiveM,    bar: data5m },
    { interval: ExchangeIntervals.fifteenM, bar: data15m },
  ] as any)

  const elapsed = ((Date.now() - startedAt) / 1000).toFixed(1)
  console.log(`   completed in ${elapsed}s`)

  if (!result) {
    console.log('   (no result)')
    return null
  }

  const f = result.financial || {}
  const n = result.numerical || {}
  const r = result.ratios || {}
  const total = n.all ?? 0
  const wins = n.profit ?? 0
  const wr = total > 0 ? (wins / total * 100).toFixed(1) : '—'
  console.log(`   ─── ${cfg.name} financials ───`)
  console.log(`     Deals total:    ${total}`)
  console.log(`     Profit / loss:  ${wins} / ${n.loss ?? 0}`)
  console.log(`     Open / closed:  ${n.open ?? 0} / ${n.closed ?? 0}`)
  console.log(`     Net profit USD: $${(f.netProfitTotalUsd ?? 0).toFixed(2)}`)
  console.log(`     Net profit %:   ${(f.netProfitTotalPerc ?? 0).toFixed(2)}%`)
  console.log(`     Max DD %:       ${(r.maxDrawdownPerc ?? r.maxDrawdown ?? 0).toFixed(2)}%`)
  console.log(`     Win rate:       ${wr}%`)
  console.log(`     Profit factor:  ${(r.profitFactor ?? 0).toFixed(2)}`)
  console.log(`     Max consec L:   ${n.maxConsecutiveLosses ?? 0}`)
  console.log(`     DCA triggered:  ${n.maxDCATriggered ?? 0} max / ${(n.avgDCATriggered ?? 0).toFixed(2)} avg`)
  return result
}

// ─── 5. Main ───
async function main() {
  const runs: RunConfig[] = [
    { name: 'v1 LONG',   direction: 'LONG',  withTimeSL: false },
    { name: 'v1 SHORT',  direction: 'SHORT', withTimeSL: false },
    { name: 'v1.1 LONG', direction: 'LONG',  withTimeSL: true  },
    { name: 'v1.1 SHORT',direction: 'SHORT', withTimeSL: true  },
  ]
  const results: Record<string, any> = {}
  for (const cfg of runs) {
    results[cfg.name] = await runBacktest(cfg)
  }

  // ── Combine LONG + SHORT for v1 and v1.1 ──
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
      const totalOpen = (lngN.open ?? 0) + (shtN.open ?? 0)
      const wr = (totalWins + totalLoss) > 0 ? (totalWins / (totalWins + totalLoss) * 100).toFixed(1) : '—'
      console.log(`\n${ver} TOTAL (LONG + SHORT):`)
      console.log(`  Net profit: $${totalUsd.toFixed(2)} ` +
                  `(${(totalUsd / STARTING_BALANCE * 100).toFixed(1)}% of $${STARTING_BALANCE})`)
      console.log(`  Trades:     ${totalDeals} total — ${totalWins}W / ${totalLoss}L / ${totalOpen} open`)
      console.log(`  Win rate:   ${wr}% (closed deals only)`)
    }
  }

  console.log('\nCompared to Python no-lookahead backtest:')
  console.log('  v1:    +269% / -8.4% DD / 55% WR / 3,183 trades')
  console.log('  v1.1:  +290% / -6.9% DD / 55% WR / 3,191 trades')
  console.log('\n(Gainium values likely lower since we are missing:')
  console.log(' - Weekend 2× sizing,  - Adaptive TP 0.5/0.25, - Daily $200 stop)')
}

main().catch(e => {
  console.error('\nFATAL:', e)
  console.error(e.stack)
  process.exit(1)
})
