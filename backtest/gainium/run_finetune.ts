/**
 * Fine-tune parameter sweep — test variations on top of strict v1 baseline.
 * Goal: find config that maximizes profit AND minimizes drawdown.
 */

import DCABacktesting from './src/dca'
import {
  ExchangeIntervals, ExchangeEnum, StrategyEnum,
  OrderTypeEnum, StartConditionEnum, OrderSizeTypeEnum,
  IndicatorEnum, IndicatorStartConditionEnum, IndicatorsLogicEnum,
  IndicatorAction, IndicatorSection, MAEnum, CooldownUnits, CloseConditionEnum,
} from './src/types'
import * as fs from 'fs'
import { v4 as uuidv4 } from 'uuid'

const CSV_PATH = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
const TEST_DAYS = 30
const INITIAL_BALANCE = 5000
const LEVERAGE = 3
const COMMISSION_PCT = 0.0004
const SLIPPAGE_BPS = 2
const BREAKER_PAUSE_MIN = 15
const DCA_LEVELS = 2

console.log(`═══ FINE-TUNE SWEEP (${TEST_DAYS}d, $${INITIAL_BALANCE}, ${LEVERAGE}×) ═══\n`)

// Load data
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

// Indicators
const atr5m = new Float64Array(data5m.length)
let prevClose = data5m[0].close, trSum = 0
for (let i = 0; i < data5m.length; i++) {
  const b = data5m[i]
  const tr = Math.max(b.high - b.low, Math.abs(b.high - prevClose), Math.abs(b.low - prevClose))
  if (i < 14) { trSum += tr; atr5m[i] = i === 13 ? trSum / 14 : 0 }
  else atr5m[i] = (atr5m[i-1] * 13 + tr) / 14
  prevClose = b.close
}
function ema(values: number[], period: number) {
  const k = 2 / (period + 1); const out = new Array(values.length).fill(0); out[0] = values[0]
  for (let i = 1; i < values.length; i++) out[i] = values[i] * k + out[i-1] * (1 - k)
  return out
}
const closes15m = data15m.map((b: any) => b.close)
const ema20_15m = ema(closes15m, 20)
const ema50_15m = ema(closes15m, 50)
const ema15mByTime = new Map<number, { ema20: number; ema50: number; trend: 'UP'|'DOWN' }>()
for (let i = 0; i < data15m.length; i++) ema15mByTime.set(data15m[i].time, { ema20: ema20_15m[i], ema50: ema50_15m[i], trend: ema20_15m[i] > ema50_15m[i] ? 'UP' : 'DOWN' })
function get15m(time: number) { return ema15mByTime.get(Math.floor(time / 900000) * 900000) ?? null }

// Get Gainium entries (cached by RSI threshold)
const entryCache = new Map<string, any[]>()
const SYMBOL: any = {
  pair: 'BTCUSDT', exchange: ExchangeEnum.bybitUsdm,
  baseAsset: { minAmount: 0.001, maxAmount: 1500, step: 0.001, name: 'BTC' },
  quoteAsset: { minAmount: 5, name: 'USDT' }, maxOrders: 200, priceAssetPrecision: 1,
}
async function getEntries(direction: 'LONG' | 'SHORT', rsiThresh: number): Promise<any[]> {
  const key = `${direction}-${rsiThresh}`
  if (entryCache.has(key)) return entryCache.get(key)!
  const gid = uuidv4()
  const longSide = direction === 'LONG'
  const bt: any = new DCABacktesting({
    exchange: ExchangeEnum.bybitUsdm, symbols: [{ ...SYMBOL }],
    interval: ExchangeIntervals.fiveM,
    balances: [{ asset: 'USDT', free: String(INITIAL_BALANCE), locked: '0' }],
    userFee: COMMISSION_PCT, slippage: SLIPPAGE_BPS / 10000,
    prices: [{ symbol: 'BTCUSDT', price: data5m[data5m.length - 1].close }],
    settings: {
      pair: ['BTCUSDT'], name: `entry-${direction}-${rsiThresh}`,
      strategy: longSide ? StrategyEnum.long : StrategyEnum.short,
      baseOrderSize: '2375', orderSize: '2375', orderSizeType: OrderSizeTypeEnum.usd,
      startOrderType: OrderTypeEnum.market, useLimitPrice: false,
      startCondition: StartConditionEnum.ti,
      useDca: false, ordersCount: '1', activeOrdersCount: '1',
      step: '0.5', stepScale: '1', volumeScale: '1',
      useTp: true, tpPerc: '50', dealCloseCondition: CloseConditionEnum.tp,
      useSl: false, useSmartOrders: false, hodlDay: '', hodlAt: '', hodlNextBuy: 0,
      futures: true, leverage: LEVERAGE,
      maxNumberOfOpenDeals: '999',
      profitCurrency: 'quote', orderFixedIn: 'quote',
      cooldownAfterDealStart: false, cooldownAfterDealStop: false,
      indicators: [
        { uuid: uuidv4(), type: IndicatorEnum.rsi, indicatorLength: 9,
          indicatorValue: longSide ? String(rsiThresh) : String(100 - rsiThresh),
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
  const entries = ((result?.deals as any[]) ?? []).map(d => ({
    side: direction, startTime: d.startTime,
    startPrice: d.filledOrders?.[0]?.price ?? d.startPrice,
  }))
  entryCache.set(key, entries)
  return entries
}

interface Cfg {
  rsiThresh: number       // e.g. 30 for LONG → RSI<30 (SHORT mirrored to 70)
  gapMinPct: number       // GAP filter
  atrMaxPct: number       // ATR filter
  dcaSpacingPct: number
  tpSinglePct: number
  tpDcaPct: number
  slFromWorstPct: number
  weekendMult: number
  dailyMaxLoss: number
  timeSLHours: number     // 0 = no time-SL
  smartTimeSL: boolean    // only fire on loss
}

function findBarIdx(time: number): number {
  let lo = 0, hi = data5m.length - 1
  while (lo < hi) { const mid = (lo + hi) >> 1; if (data5m[mid].time < time) lo = mid + 1; else hi = mid }
  return lo
}

function getFilterPass(atrMax: number, gapMin: number): Set<number> {
  const set = new Set<number>()
  for (let i = 0; i < data5m.length; i++) {
    const b = data5m[i]
    if (atr5m[i] === 0) continue
    if ((atr5m[i] / b.close) * 100 > atrMax) continue
    const e = get15m(b.time)
    if (!e || e.ema50 === 0) continue
    if (Math.abs((e.ema20 - e.ema50) / e.ema50) * 100 < gapMin) continue
    set.add(b.time)
  }
  return set
}

function simulateDeal(entryTime: number, entryPrice: number, side: 'LONG'|'SHORT', cfg: Cfg) {
  const startIdx = findBarIdx(entryTime)
  if (startIdx >= data5m.length - 1) return null
  const sign = side === 'LONG' ? 1 : -1
  const l1Price = entryPrice
  let worstPrice = l1Price
  let avgPrice = l1Price
  let legs = 1
  const dcaPrice = side === 'LONG'
    ? l1Price * (1 - cfg.dcaSpacingPct / 100)
    : l1Price * (1 + cfg.dcaSpacingPct / 100)
  const perLegMargin = (INITIAL_BALANCE * 0.95 * LEVERAGE) / LEVERAGE / DCA_LEVELS
  const perLegNotional = perLegMargin * LEVERAGE
  const perLegQty = perLegNotional / l1Price
  const dow = new Date(entryTime).getUTCDay()
  const weekend = (dow === 0 || dow === 6)
  const qtyMult = weekend ? cfg.weekendMult : 1.0
  let totalQty = perLegQty * qtyMult
  const entryTrend = get15m(entryTime)?.trend
  const maxBars = cfg.timeSLHours > 0 ? Math.ceil(cfg.timeSLHours * 60 / 5) : data5m.length

  for (let i = startIdx + 1; i < data5m.length; i++) {
    const bar = data5m[i]
    const barsHeld = i - startIdx
    if (legs === 1) {
      const dcaHit = side === 'LONG' ? (bar.low <= dcaPrice) : (bar.high >= dcaPrice)
      if (dcaHit) {
        legs = 2; worstPrice = dcaPrice
        avgPrice = (l1Price + dcaPrice) / 2
        const l2Qty = (perLegNotional / dcaPrice) * qtyMult
        totalQty += l2Qty
      }
    }
    if (cfg.timeSLHours > 0 && barsHeld >= maxBars) {
      const exitPx = bar.close
      const grossPnl = (exitPx - avgPrice) * totalQty * sign
      const fees = exitPx * totalQty * COMMISSION_PCT
      const netPnl = grossPnl - fees
      if (!cfg.smartTimeSL || netPnl < 0) {
        return { entry: entryTime, exit: bar.time, reason: 'TIME_SL',
                 pnlPctOnEquity: netPnl / INITIAL_BALANCE * 100, weekend }
      }
    }
    const tpPct = legs === 2 ? cfg.tpDcaPct : cfg.tpSinglePct
    const tpPrice = side === 'LONG' ? avgPrice * (1 + tpPct / 100) : avgPrice * (1 - tpPct / 100)
    const tpHit = side === 'LONG' ? (bar.high >= tpPrice) : (bar.low <= tpPrice)
    if (tpHit) {
      const grossPnl = (tpPrice - avgPrice) * totalQty * sign
      const fees = tpPrice * totalQty * COMMISSION_PCT
      const netPnl = grossPnl - fees
      return { entry: entryTime, exit: bar.time, reason: 'TP',
               pnlPctOnEquity: netPnl / INITIAL_BALANCE * 100, weekend }
    }
    let slPrice = legs === 2 ? avgPrice
      : (side === 'LONG' ? worstPrice * (1 - cfg.slFromWorstPct / 100) : worstPrice * (1 + cfg.slFromWorstPct / 100))
    const slHit = side === 'LONG' ? (bar.low <= slPrice) : (bar.high >= slPrice)
    if (slHit) {
      const exitPx = slPrice
      const grossPnl = (exitPx - avgPrice) * totalQty * sign
      const fees = exitPx * totalQty * COMMISSION_PCT
      const netPnl = grossPnl - fees
      return { entry: entryTime, exit: bar.time, reason: legs === 2 ? 'BE-DCA' : 'SL',
               pnlPctOnEquity: netPnl / INITIAL_BALANCE * 100, weekend }
    }
    const trendNow = get15m(bar.time)?.trend
    if (entryTrend && trendNow && trendNow !== entryTrend) {
      const exitPx = bar.close
      const grossPnl = (exitPx - avgPrice) * totalQty * sign
      const fees = exitPx * totalQty * COMMISSION_PCT
      const netPnl = grossPnl - fees
      return { entry: entryTime, exit: bar.time, reason: 'TREND_FLIP',
               pnlPctOnEquity: netPnl / INITIAL_BALANCE * 100, weekend }
    }
  }
  return null  // didn't close in window
}

async function runCfg(cfg: Cfg): Promise<{ profit: number; ret: number; trades: number; wr: number; maxDD: number; pf: number; expectancy: number }> {
  const longRsi = cfg.rsiThresh
  const longEntries = await getEntries('LONG', longRsi)
  const shortEntries = await getEntries('SHORT', longRsi)
  const allEntries = [...longEntries, ...shortEntries].sort((a, b) => a.startTime - b.startTime)
  const filterPass = getFilterPass(cfg.atrMaxPct, cfg.gapMinPct)
  let balance = INITIAL_BALANCE, peak = balance, maxDD = 0
  const dailyLoss = new Map<string, number>()
  let openUntil = 0, lossCooldownUntil = 0
  let wins = 0, losses = 0, nKept = 0
  let totalW = 0, totalL = 0
  for (const e of allEntries) {
    if (e.startTime < openUntil) continue
    if (e.startTime < lossCooldownUntil) continue
    if (!filterPass.has(e.startTime)) continue
    const day = new Date(e.startTime).toISOString().slice(0, 10)
    const dl = dailyLoss.get(day) ?? 0
    if (dl <= -cfg.dailyMaxLoss) continue
    nKept++
    const sim = simulateDeal(e.startTime, e.startPrice, e.side, cfg)
    if (!sim) continue
    const scaled = sim.pnlPctOnEquity * (balance / INITIAL_BALANCE) / 100 * INITIAL_BALANCE
    balance += scaled
    if (balance > peak) peak = balance
    const dd = (peak - balance) / peak * 100; if (dd > maxDD) maxDD = dd
    if (scaled > 0) { wins++; totalW += scaled }
    else if (scaled < 0) { losses++; totalL += scaled; lossCooldownUntil = sim.exit + BREAKER_PAUSE_MIN * 60 * 1000 }
    dailyLoss.set(day, dl + Math.min(0, scaled))
    openUntil = sim.exit
  }
  const profit = balance - INITIAL_BALANCE
  const ret = profit / INITIAL_BALANCE * 100
  const wr = (wins + losses) > 0 ? wins / (wins + losses) * 100 : 0
  const pf = totalL < 0 ? Math.abs(totalW / totalL) : Infinity
  const expectancy = (wins + losses) > 0 ? profit / (wins + losses) : 0
  return { profit, ret, trades: wins + losses, wr, maxDD, pf, expectancy }
}

// Baseline (current v1.1 SMART config)
const BASELINE: Cfg = {
  rsiThresh: 30, gapMinPct: 0.25, atrMaxPct: 0.6,
  dcaSpacingPct: 0.5, tpSinglePct: 0.5, tpDcaPct: 0.25, slFromWorstPct: 0.6,
  weekendMult: 3, dailyMaxLoss: 200,
  timeSLHours: 6, smartTimeSL: true,
}

interface Result extends Cfg { profit: number; ret: number; trades: number; wr: number; maxDD: number; pf: number; expectancy: number; tag: string }

async function main() {
  console.log('1. Running baseline...')
  const baseRes = await runCfg(BASELINE)
  console.log(`   v1.1 SMART baseline: $${baseRes.profit.toFixed(0)} (${baseRes.ret.toFixed(1)}%) WR ${baseRes.wr.toFixed(0)}% PF ${baseRes.pf.toFixed(2)} DD ${baseRes.maxDD.toFixed(2)}% / ${baseRes.trades} trades`)
  console.log()

  console.log('2. Single-parameter variations...\n')
  const variations: { name: string; cfg: Cfg }[] = [
    // RSI threshold
    { name: 'RSI tighter (25/75)', cfg: { ...BASELINE, rsiThresh: 25 } },
    { name: 'RSI 28/72', cfg: { ...BASELINE, rsiThresh: 28 } },
    { name: 'RSI 32/68', cfg: { ...BASELINE, rsiThresh: 32 } },
    { name: 'RSI 35/65', cfg: { ...BASELINE, rsiThresh: 35 } },
    // GAP
    { name: 'GAP 0.15%', cfg: { ...BASELINE, gapMinPct: 0.15 } },
    { name: 'GAP 0.20%', cfg: { ...BASELINE, gapMinPct: 0.20 } },
    { name: 'GAP 0.35%', cfg: { ...BASELINE, gapMinPct: 0.35 } },
    { name: 'GAP 0.50%', cfg: { ...BASELINE, gapMinPct: 0.50 } },
    // ATR
    { name: 'ATR 0.4%', cfg: { ...BASELINE, atrMaxPct: 0.4 } },
    { name: 'ATR 0.5%', cfg: { ...BASELINE, atrMaxPct: 0.5 } },
    { name: 'ATR 0.8%', cfg: { ...BASELINE, atrMaxPct: 0.8 } },
    { name: 'ATR 1.0%', cfg: { ...BASELINE, atrMaxPct: 1.0 } },
    // DCA spacing
    { name: 'DCA 0.3%', cfg: { ...BASELINE, dcaSpacingPct: 0.3 } },
    { name: 'DCA 0.7%', cfg: { ...BASELINE, dcaSpacingPct: 0.7 } },
    { name: 'DCA 1.0%', cfg: { ...BASELINE, dcaSpacingPct: 1.0 } },
    // TP
    { name: 'TP 0.4/0.2%', cfg: { ...BASELINE, tpSinglePct: 0.4, tpDcaPct: 0.2 } },
    { name: 'TP 0.6/0.3%', cfg: { ...BASELINE, tpSinglePct: 0.6, tpDcaPct: 0.3 } },
    { name: 'TP 0.7/0.35%', cfg: { ...BASELINE, tpSinglePct: 0.7, tpDcaPct: 0.35 } },
    { name: 'TP 0.5/0.5% (same)', cfg: { ...BASELINE, tpSinglePct: 0.5, tpDcaPct: 0.5 } },
    // SL from worst
    { name: 'SL 0.4%', cfg: { ...BASELINE, slFromWorstPct: 0.4 } },
    { name: 'SL 0.8%', cfg: { ...BASELINE, slFromWorstPct: 0.8 } },
    // Weekend mult
    { name: 'Weekend 2×', cfg: { ...BASELINE, weekendMult: 2 } },
    { name: 'Weekend 4×', cfg: { ...BASELINE, weekendMult: 4 } },
    { name: 'Weekend 5×', cfg: { ...BASELINE, weekendMult: 5 } },
    // Time-SL
    { name: 'No time-SL', cfg: { ...BASELINE, timeSLHours: 0 } },
    { name: 'Time-SL 4h', cfg: { ...BASELINE, timeSLHours: 4 } },
    { name: 'Time-SL 8h', cfg: { ...BASELINE, timeSLHours: 8 } },
    { name: 'Time-SL 12h', cfg: { ...BASELINE, timeSLHours: 12 } },
    { name: 'Time-SL 6h ALWAYS (not smart)', cfg: { ...BASELINE, smartTimeSL: false } },
  ]
  const results: Result[] = [{ ...BASELINE, ...baseRes, tag: 'BASELINE v1.1 SMART' }]
  for (const v of variations) {
    const r = await runCfg(v.cfg)
    results.push({ ...v.cfg, ...r, tag: v.name })
    const delta = r.profit - baseRes.profit
    const arrow = delta > 5 ? '🟢' : delta < -5 ? '🔴' : '⚪'
    console.log(`   ${arrow} ${v.name.padEnd(35)} $${r.profit.toFixed(0).padStart(5)} (${r.ret.toFixed(1).padStart(5)}%) WR ${r.wr.toFixed(0).padStart(3)}% PF ${r.pf.toFixed(2)} DD ${r.maxDD.toFixed(2)}% trades=${r.trades}`)
  }

  // Sort by profit
  results.sort((a, b) => b.profit - a.profit)
  console.log(`\n═══ TOP 10 CONFIGS (sorted by profit) ═══`)
  console.log('Rank | Config                                | Profit | Return | WR  | PF   | MaxDD | Trades')
  console.log('-----+----------------------------------------+--------+--------+-----+------+-------+-------')
  for (let i = 0; i < Math.min(10, results.length); i++) {
    const r = results[i]
    console.log(`${String(i+1).padStart(4)} | ${r.tag.padEnd(38)} | $${r.profit.toFixed(0).padStart(5)} | ${r.ret.toFixed(1).padStart(5)}% | ${r.wr.toFixed(0).padStart(3)}% | ${r.pf.toFixed(2).padStart(4)} | ${r.maxDD.toFixed(2).padStart(4)}% | ${String(r.trades).padStart(5)}`)
  }
}

main().catch(e => { console.error(e); process.exit(1) })
