/**
 * Parameter sweep — find best v1 / v1.1 config.
 * Tests multiple combinations and ranks by net profit.
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

const CSV_PATH = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
const TEST_DAYS = 30
const INITIAL_BALANCE = 5000
const LEVERAGE = 3

// ─── Load data ───
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

// ─── Pre-compute ATR + EMA for custom filters ───
const atr5m = new Float64Array(data5m.length)
let prevClose = data5m[0].close; let trSum = 0
for (let i = 0; i < data5m.length; i++) {
  const b = data5m[i]
  const tr = Math.max(b.high - b.low, Math.abs(b.high - prevClose), Math.abs(b.low - prevClose))
  if (i < 14) { trSum += tr; atr5m[i] = i === 13 ? trSum / 14 : 0 }
  else atr5m[i] = (atr5m[i-1] * 13 + tr) / 14
  prevClose = b.close
}
function ema(values: number[], period: number): number[] {
  const k = 2 / (period + 1); const out = new Array(values.length).fill(0); out[0] = values[0]
  for (let i = 1; i < values.length; i++) out[i] = values[i] * k + out[i-1] * (1 - k)
  return out
}
const closes15m = data15m.map((b: any) => b.close)
const ema20_15m = ema(closes15m, 20)
const ema50_15m = ema(closes15m, 50)
const ema15mByTime = new Map<number, { ema20: number; ema50: number }>()
for (let i = 0; i < data15m.length; i++) ema15mByTime.set(data15m[i].time, { ema20: ema20_15m[i], ema50: ema50_15m[i] })

// Filter sets per (atrMaxPct, gapMinPct) so we can sweep them
const filterCache = new Map<string, Set<number>>()
function getFilterPass(atrMaxPct: number, gapMinPct: number): Set<number> {
  const key = `${atrMaxPct}:${gapMinPct}`
  if (filterCache.has(key)) return filterCache.get(key)!
  const pass = new Set<number>()
  for (let i = 0; i < data5m.length; i++) {
    const b = data5m[i]
    const atrVal = atr5m[i]; if (atrVal === 0) continue
    if ((atrVal / b.close) * 100 > atrMaxPct) continue
    const bucket = Math.floor(b.time / 900000) * 900000
    const emas = ema15mByTime.get(bucket)
    if (!emas || emas.ema50 === 0) continue
    if (Math.abs((emas.ema20 - emas.ema50) / emas.ema50) * 100 < gapMinPct) continue
    pass.add(b.time)
  }
  filterCache.set(key, pass)
  return pass
}

// ─── Bot config builder ───
const SYMBOL: any = {
  pair: 'BTCUSDT', exchange: ExchangeEnum.bybitUsdm,
  baseAsset: { minAmount: 0.001, maxAmount: 1500, step: 0.001, name: 'BTC' },
  quoteAsset: { minAmount: 5, name: 'USDT' }, maxOrders: 200, priceAssetPrecision: 1,
}

interface BotCfg {
  direction: 'LONG' | 'SHORT'
  tpPct: number
  slPct: number
  dcaSpacingPct: number
  timeSLHours: number  // 0 = no time-SL
}

function makeSettings(c: BotCfg): any {
  const longSide = c.direction === 'LONG'
  const gid = uuidv4()
  const perLegMargin = (INITIAL_BALANCE * 0.95 * LEVERAGE) / LEVERAGE / 2
  return {
    name: 'sweep', pair: ['BTCUSDT'],
    strategy: longSide ? StrategyEnum.long : StrategyEnum.short,
    baseOrderSize: String(perLegMargin.toFixed(0)), orderSize: String(perLegMargin.toFixed(0)),
    orderSizeType: OrderSizeTypeEnum.usd,
    startOrderType: OrderTypeEnum.market, useLimitPrice: false,
    startCondition: StartConditionEnum.ti,
    useDca: true, ordersCount: '2', activeOrdersCount: '2',
    step: String(c.dcaSpacingPct), stepScale: '1', volumeScale: '1',
    useTp: true, tpPerc: String(c.tpPct), dealCloseCondition: CloseConditionEnum.tp,
    useSl: true, slPerc: String(c.slPct), baseSlOn: 'start' as any,
    moveSL: true, moveSLTrigger: String(c.dcaSpacingPct), moveSLValue: '0',
    ...(c.timeSLHours > 0 ? {
      closeByTimer: true, closeByTimerValue: c.timeSLHours,
      closeByTimerUnits: CooldownUnits.hours,
    } : {}),
    futures: true, leverage: LEVERAGE,
    maxNumberOfOpenDeals: '1',
    profitCurrency: 'quote', orderFixedIn: 'quote',
    cooldownAfterDealStart: false, cooldownAfterDealStop: false,
    useSmartOrders: false, hodlDay: '', hodlAt: '', hodlNextBuy: 0,
    indicators: [
      { uuid: uuidv4(), type: IndicatorEnum.rsi, indicatorLength: 9,
        indicatorValue: longSide ? '30' : '70',
        indicatorCondition: longSide ? IndicatorStartConditionEnum.lt : IndicatorStartConditionEnum.gt,
        indicatorInterval: ExchangeIntervals.fiveM, indicatorAction: IndicatorAction.startDeal, groupId: gid },
      { uuid: uuidv4(), type: IndicatorEnum.ma, maType: MAEnum.ema, indicatorLength: 20,
        indicatorValue: 'crossing', maCrossingValue: MAEnum.ema, maCrossingLength: 50,
        maCrossingInterval: ExchangeIntervals.fifteenM, maUUID: uuidv4(),
        indicatorCondition: longSide ? IndicatorStartConditionEnum.gt : IndicatorStartConditionEnum.lt,
        indicatorInterval: ExchangeIntervals.fifteenM, indicatorAction: IndicatorAction.startDeal, groupId: gid },
    ],
    indicatorGroups: [{ id: gid, logic: IndicatorsLogicEnum.and, action: IndicatorAction.startDeal, section: IndicatorSection.controller }],
  }
}

async function runDealsOnce(cfg: BotCfg): Promise<any[]> {
  const bt: any = new DCABacktesting({
    exchange: ExchangeEnum.bybitUsdm, symbols: [{ ...SYMBOL }],
    interval: ExchangeIntervals.fiveM,
    balances: [{ asset: 'USDT', free: String(INITIAL_BALANCE), locked: '0' }],
    userFee: 0.0004, slippage: 0.0002,
    prices: [{ symbol: 'BTCUSDT', price: data5m[data5m.length - 1].close }],
    settings: makeSettings(cfg), fullResult: true,
  } as any)
  const result = await bt.test([
    { interval: ExchangeIntervals.fiveM, bar: data5m },
    { interval: ExchangeIntervals.fifteenM, bar: data15m },
  ] as any)
  return (result?.deals as any[]) ?? []
}

interface FilterCfg {
  atrMaxPct: number
  gapMinPct: number
  weekendMult: number
  dailyMaxLoss: number
  postLossCooldownMin: number
}

function applyFilters(longDeals: any[], shortDeals: any[], f: FilterCfg, label: string): { ret: number; profit: number; balance: number; trades: number; wr: number; maxDD: number; openStuck: number; unrealized: number } {
  const allDeals = [
    ...longDeals.map(d => ({ ...d, side: 'LONG' })),
    ...shortDeals.map(d => ({ ...d, side: 'SHORT' })),
  ].sort((a, b) => a.startTime - b.startTime)
  const filterPass = getFilterPass(f.atrMaxPct, f.gapMinPct)
  const lastBarPrice = data5m[data5m.length - 1].close
  let balance = INITIAL_BALANCE, peak = balance, maxDD = 0
  const dailyLoss = new Map<string, number>()
  let nKept = 0, nWins = 0, nLosses = 0, lossCooldownUntil = 0
  let openStuck = 0, unrealized = 0
  for (const d of allDeals) {
    if (d.startTime < lossCooldownUntil) continue
    if (!filterPass.has(d.startTime)) continue
    // HONEST: count open positions' mark-to-market
    if (d.status !== 'closed') {
      if (d.filledOrders?.length && d.avgPrice) {
        const qty = d.filledOrders.reduce((acc: number, o: any) => acc + (o.qty || 0), 0)
        const sideMult = d.side === 'LONG' ? 1 : -1
        const mtm = (lastBarPrice - d.avgPrice) * qty * sideMult - lastBarPrice * qty * 0.0004 * 2
        // Apply weekend multiplier + compounding scaling consistent with closed deals
        const dow0 = new Date(d.startTime).getUTCDay()
        const mtmScaled = mtm * (dow0 === 0 || dow0 === 6 ? f.weekendMult : 1) * (balance / INITIAL_BALANCE)
        if (mtmScaled < 0) { openStuck++; unrealized += mtmScaled }
      }
      continue
    }
    const day = new Date(d.startTime).toISOString().slice(0, 10)
    const dl = dailyLoss.get(day) ?? 0
    if (dl <= -f.dailyMaxLoss) continue
    nKept++
    let rawProfit = d.profit?.totalUsd ?? d.profit?.total ?? 0
    const dow = new Date(d.startTime).getUTCDay()
    if (dow === 0 || dow === 6) rawProfit *= f.weekendMult
    const scaled = rawProfit * (balance / INITIAL_BALANCE)
    balance += scaled
    if (balance > peak) peak = balance
    const dd = (peak - balance) / peak * 100; if (dd > maxDD) maxDD = dd
    if (scaled > 0) nWins++
    else if (scaled < 0) {
      nLosses++
      lossCooldownUntil = (d.closedTime ?? d.startTime) + f.postLossCooldownMin * 60 * 1000
    }
    dailyLoss.set(day, dl + Math.min(0, scaled))
  }
  // HONEST balance = realized + unrealized
  const honestBalance = balance + unrealized
  const honestProfit = honestBalance - INITIAL_BALANCE
  const wr = (nWins + nLosses) > 0 ? nWins / (nWins + nLosses) * 100 : 0
  return { ret: honestProfit / INITIAL_BALANCE * 100, profit: honestProfit, balance: honestBalance, trades: nKept, wr, maxDD, openStuck, unrealized }
}

// ─── Sweep ───
async function main() {
  console.log(`═══ PARAMETER SWEEP — ${TEST_DAYS}d, $${INITIAL_BALANCE}, ${LEVERAGE}× ═══\n`)

  // Generate bot configs to test
  const botCfgs: BotCfg[] = []
  const tpValues = [0.4, 0.5]
  const slValues = [0.6, 0.8]
  const dcaSpacings = [0.5]
  const timeSLs = [0, 6, 12, 24]   // 0 = no time-SL (v1), 6h = v1.1, 12h, 24h
  for (const tp of tpValues)
    for (const sl of slValues)
      for (const dca of dcaSpacings)
        for (const tsl of timeSLs)
          for (const dir of ['LONG', 'SHORT'] as const)
            botCfgs.push({ direction: dir, tpPct: tp, slPct: sl, dcaSpacingPct: dca, timeSLHours: tsl })

  console.log(`Running ${botCfgs.length} bot variants...`)
  // Run each unique (LONG, SHORT) config pair
  type RunKey = string
  const dealsCache = new Map<RunKey, any[]>()
  const keyOf = (c: BotCfg) => `${c.direction}-${c.tpPct}-${c.slPct}-${c.dcaSpacingPct}-${c.timeSLHours}`
  let n = 0
  for (const cfg of botCfgs) {
    const k = keyOf(cfg)
    if (dealsCache.has(k)) continue
    dealsCache.set(k, await runDealsOnce(cfg))
    n++
    if (n % 4 === 0) process.stdout.write(`  ${n}/${botCfgs.length}\r`)
  }
  console.log(`  ${n}/${botCfgs.length} runs done\n`)

  // Filter combinations
  const filterCfgs: FilterCfg[] = []
  for (const atr of [0.6, 0.8])
    for (const gap of [0.25, 0.5])
      for (const wkd of [2, 3])
        for (const dailyStop of [200, 9999])
          for (const cdMin of [15])
            filterCfgs.push({ atrMaxPct: atr, gapMinPct: gap, weekendMult: wkd, dailyMaxLoss: dailyStop, postLossCooldownMin: cdMin })

  // Test each combination of (timeSL, tp, sl, dca, filter)
  console.log(`Evaluating ${tpValues.length * slValues.length * dcaSpacings.length * timeSLs.length * filterCfgs.length} combinations...\n`)
  type Result = {
    tag: string
    tp: number; sl: number; dca: number; timeSL: number
    atr: number; gap: number; wkd: number; dStop: number
    profit: number; ret: number; trades: number; wr: number; maxDD: number; balance: number
    openStuck: number; unrealized: number
  }
  const results: Result[] = []
  for (const tp of tpValues)
    for (const sl of slValues)
      for (const dca of dcaSpacings)
        for (const tsl of timeSLs) {
          const longDeals = dealsCache.get(`LONG-${tp}-${sl}-${dca}-${tsl}`) ?? []
          const shortDeals = dealsCache.get(`SHORT-${tp}-${sl}-${dca}-${tsl}`) ?? []
          for (const f of filterCfgs) {
            const r = applyFilters(longDeals, shortDeals, f, '')
            results.push({
              tag: `tp${tp}_sl${sl}_dca${dca}_tsl${tsl}_atr${f.atrMaxPct}_gap${f.gapMinPct}_wkd${f.weekendMult}_dStop${f.dailyMaxLoss}`,
              tp, sl, dca, timeSL: tsl,
              atr: f.atrMaxPct, gap: f.gapMinPct, wkd: f.weekendMult, dStop: f.dailyMaxLoss,
              profit: r.profit, ret: r.ret, trades: r.trades, wr: r.wr, maxDD: r.maxDD, balance: r.balance,
              openStuck: r.openStuck, unrealized: r.unrealized,
            })
          }
        }

  // Sort by HONEST profit (includes unrealized losses)
  results.sort((a, b) => b.profit - a.profit)
  console.log(`═══ TOP 15 CONFIGS (HONEST profit, ${TEST_DAYS}d compounded, includes unrealized losses) ═══\n`)
  console.log('Rank | Profit  | %     | Trades | WR    | StuckOpen | tp   sl   dca  timeSL | atr  gap   wkd dStop')
  console.log('-----|---------|-------|--------|-------|-----------|----------------------|--------------------')
  for (let i = 0; i < Math.min(15, results.length); i++) {
    const r = results[i]
    console.log(
      `${String(i+1).padStart(4)}|`,
      `$${r.profit.toFixed(0).padStart(6)} | ` +
      `${r.ret.toFixed(1).padStart(5)}% |` +
      ` ${String(r.trades).padStart(5)}  |` +
      ` ${r.wr.toFixed(0).padStart(4)}% |` +
      ` ${String(r.openStuck).padStart(3)} ($${r.unrealized.toFixed(0).padStart(5)})|` +
      ` ${r.tp.toFixed(1)}  ${r.sl.toFixed(1)}  ${r.dca.toFixed(1)}  ${String(r.timeSL).padStart(2)}h   |` +
      ` ${r.atr.toFixed(1)}  ${r.gap.toFixed(2)}  ${r.wkd}×  $${r.dStop}`
    )
  }

  console.log(`\n═══ COMPARISON: v1 vs v1.1 baselines ═══`)
  const v1Baseline = results.find(r => r.tp === 0.5 && r.sl === 0.6 && r.timeSL === 0 && r.atr === 0.6 && r.gap === 0.25 && r.wkd === 2 && r.dStop === 200)
  const v11Baseline = results.find(r => r.tp === 0.5 && r.sl === 0.6 && r.timeSL === 6 && r.atr === 0.6 && r.gap === 0.25 && r.wkd === 2 && r.dStop === 200)
  if (v1Baseline) console.log(`v1   (tp 0.5%, sl 0.6%, no time-SL, wkd 2×, $200 stop): $${v1Baseline.profit.toFixed(0)} (${v1Baseline.ret.toFixed(1)}%) WR ${v1Baseline.wr.toFixed(0)}% MaxDD ${v1Baseline.maxDD.toFixed(1)}%`)
  if (v11Baseline) console.log(`v1.1 (tp 0.5%, sl 0.6%, 6h time-SL, wkd 2×, $200 stop):  $${v11Baseline.profit.toFixed(0)} (${v11Baseline.ret.toFixed(1)}%) WR ${v11Baseline.wr.toFixed(0)}% MaxDD ${v11Baseline.maxDD.toFixed(1)}%`)
  const best = results[0]
  console.log(`BEST: tp ${best.tp}% sl ${best.sl}% timeSL ${best.timeSL}h atr ${best.atr}% gap ${best.gap}% wkd ${best.wkd}× dStop $${best.dStop}`)
  console.log(`      $${best.profit.toFixed(0)} (${best.ret.toFixed(1)}%) WR ${best.wr.toFixed(0)}% MaxDD ${best.maxDD.toFixed(1)}% with ${best.trades} trades`)
}

main().catch(e => { console.error(e); process.exit(1) })
