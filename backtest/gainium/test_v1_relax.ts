// Progressive relaxation test to isolate which v1 indicator config fails

import DCABacktesting from './src/dca'
import {
  ExchangeIntervals, ExchangeEnum, StrategyEnum,
  OrderTypeEnum, StartConditionEnum, OrderSizeTypeEnum,
  IndicatorEnum, IndicatorStartConditionEnum, IndicatorsLogicEnum,
  IndicatorAction, IndicatorSection, MAEnum,
} from './src/types'
import * as fs from 'fs'
import { v4 as uuidv4 } from 'uuid'

const CSV = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
const lines = fs.readFileSync(CSV, 'utf-8').trim().split('\n')
const bars: any[] = []
for (let i = 1; i < lines.length; i++) {
  const c = lines[i].split(',')
  bars.push({
    time: Math.floor(new Date(c[0]).getTime() / 1000) * 1000,
    open: +c[1], high: +c[2], low: +c[3], close: +c[4], volume: +c[5],
    symbol: 'BTCUSDT', isFinal: true,
  })
}
const cutoff = bars[bars.length-1].time - 90 * 86400 * 1000
const data5m = bars.filter(b => b.time >= cutoff)

const data15m: any[] = []
let buck: any = null
for (const b of data5m) {
  const t = Math.floor(b.time / 900000) * 900000
  if (!buck || buck.time !== t) {
    if (buck) data15m.push(buck)
    buck = { time: t, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume, symbol: 'BTCUSDT', isFinal: true }
  } else {
    buck.high = Math.max(buck.high, b.high); buck.low = Math.min(buck.low, b.low); buck.close = b.close; buck.volume += b.volume
  }
}
if (buck) data15m.push(buck)
console.log(`Loaded ${data5m.length} × 5m, ${data15m.length} × 15m bars (90d window)\n`)

async function run(name: string, makeIndicators: (gid: string) => any[]) {
  const gid = uuidv4()
  const settings: any = {
    pair: ['BTCUSDT'], name, strategy: StrategyEnum.long,
    baseOrderSize: '100', orderSize: '100', orderSizeType: OrderSizeTypeEnum.usd,
    startOrderType: OrderTypeEnum.market, startCondition: StartConditionEnum.ti,
    useDca: true, ordersCount: '2', activeOrdersCount: '2', step: '0.5', stepScale: '1', volumeScale: '1',
    useTp: true, tpPerc: '0.5', useSl: true, slPerc: '1.0', baseSlOn: 'avg',
    useSmartOrders: false, hodlDay: '', hodlAt: '', hodlNextBuy: 0,
    maxNumberOfOpenDeals: '5',
    profitCurrency: 'quote', orderFixedIn: 'quote',
    indicators: makeIndicators(gid),
    indicatorGroups: [{ id: gid, logic: IndicatorsLogicEnum.and, action: IndicatorAction.startDeal, section: IndicatorSection.controller }],
  }
  const bt: any = new DCABacktesting({
    exchange: ExchangeEnum.bybit,
    symbols: [{
      pair: 'BTCUSDT', exchange: ExchangeEnum.bybit,
      baseAsset: { minAmount: 0.001, maxAmount: 1500, step: 0.001, name: 'BTC' },
      quoteAsset: { minAmount: 5, name: 'USDT' }, maxOrders: 200, priceAssetPrecision: 1,
    }],
    interval: ExchangeIntervals.fiveM,
    balances: [{ asset: 'USDT', free: '5000', locked: '0' }],
    userFee: 0.055, slippage: 0.02,
    prices: [{ symbol: 'BTCUSDT', price: data5m[data5m.length-1].close }],
    settings,
  } as any)
  const r = await bt.test([
    { interval: ExchangeIntervals.fiveM, bar: data5m },
    { interval: ExchangeIntervals.fifteenM, bar: data15m },
  ] as any)
  console.log(`  ${name}: deals=${r?.deals?.length ?? 0}, indicatorsEvents=${r?.indicatorsEvents?.length ?? 0}, netProfit$=${r?.financial?.netProfitTotalUsd?.toFixed?.(2) ?? '-'}`)
}

(async () => {
  // Test 1: just RSI<30 on 5m
  await run('1. RSI<30 only', g => [{
    uuid: uuidv4(), type: IndicatorEnum.rsi, indicatorLength: 9, indicatorValue: '30',
    indicatorCondition: IndicatorStartConditionEnum.lt, indicatorInterval: ExchangeIntervals.fiveM,
    indicatorAction: IndicatorAction.startDeal, groupId: g,
  }])

  // Test 2: RSI<30 + ATR<0.6
  await run('2. RSI<30 + ATR<0.6', g => [
    { uuid: uuidv4(), type: IndicatorEnum.rsi, indicatorLength: 9, indicatorValue: '30',
      indicatorCondition: IndicatorStartConditionEnum.lt, indicatorInterval: ExchangeIntervals.fiveM,
      indicatorAction: IndicatorAction.startDeal, groupId: g },
    { uuid: uuidv4(), type: IndicatorEnum.atr, indicatorLength: 14, indicatorValue: '0.6',
      indicatorCondition: IndicatorStartConditionEnum.lt, indicatorInterval: ExchangeIntervals.fiveM,
      indicatorAction: IndicatorAction.startDeal, groupId: g },
  ])

  // Test 3: RSI<30 + MA(15m)
  await run('3. RSI<30 + EMA20>50 (15m)', g => [
    { uuid: uuidv4(), type: IndicatorEnum.rsi, indicatorLength: 9, indicatorValue: '30',
      indicatorCondition: IndicatorStartConditionEnum.lt, indicatorInterval: ExchangeIntervals.fiveM,
      indicatorAction: IndicatorAction.startDeal, groupId: g },
    { uuid: uuidv4(), type: IndicatorEnum.ma, maType: MAEnum.ema, indicatorLength: 20,
      indicatorValue: 'crossing', maCrossingValue: MAEnum.ema, maCrossingLength: 50,
      maCrossingInterval: ExchangeIntervals.fifteenM, maUUID: uuidv4(),
      indicatorCondition: IndicatorStartConditionEnum.gt, indicatorInterval: ExchangeIntervals.fifteenM,
      indicatorAction: IndicatorAction.startDeal, groupId: g },
  ])

  // Test 4: ALL THREE (full v1)
  await run('4. ALL THREE (full v1 LONG)', g => [
    { uuid: uuidv4(), type: IndicatorEnum.rsi, indicatorLength: 9, indicatorValue: '30',
      indicatorCondition: IndicatorStartConditionEnum.lt, indicatorInterval: ExchangeIntervals.fiveM,
      indicatorAction: IndicatorAction.startDeal, groupId: g },
    { uuid: uuidv4(), type: IndicatorEnum.ma, maType: MAEnum.ema, indicatorLength: 20,
      indicatorValue: 'crossing', maCrossingValue: MAEnum.ema, maCrossingLength: 50,
      maCrossingInterval: ExchangeIntervals.fifteenM, maUUID: uuidv4(),
      indicatorCondition: IndicatorStartConditionEnum.gt, indicatorInterval: ExchangeIntervals.fifteenM,
      indicatorAction: IndicatorAction.startDeal, groupId: g },
    { uuid: uuidv4(), type: IndicatorEnum.atr, indicatorLength: 14, indicatorValue: '0.6',
      indicatorCondition: IndicatorStartConditionEnum.lt, indicatorInterval: ExchangeIntervals.fiveM,
      indicatorAction: IndicatorAction.startDeal, groupId: g },
  ])
})().catch(e => { console.error(e); process.exit(1) })
