// Inspect result structure of a small backtest

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

const gid = uuidv4()
const bt: any = new DCABacktesting({
  exchange: ExchangeEnum.bybit,
  symbols: [{ pair: 'BTCUSDT', exchange: ExchangeEnum.bybit,
    baseAsset: { minAmount: 0.001, maxAmount: 1500, step: 0.001, name: 'BTC' },
    quoteAsset: { minAmount: 5, name: 'USDT' }, maxOrders: 200, priceAssetPrecision: 1,
  }],
  interval: ExchangeIntervals.fiveM,
  balances: [{ asset: 'USDT', free: '5000', locked: '0' }],
  userFee: 0.055, slippage: 0.02,
  prices: [{ symbol: 'BTCUSDT', price: data5m[data5m.length-1].close }],
  settings: {
    pair: ['BTCUSDT'], name: 'inspect', strategy: StrategyEnum.long,
    baseOrderSize: '100', orderSize: '100', orderSizeType: OrderSizeTypeEnum.usd,
    startOrderType: OrderTypeEnum.market, startCondition: StartConditionEnum.ti,
    useDca: true, ordersCount: '2', activeOrdersCount: '2', step: '0.5', stepScale: '1', volumeScale: '1',
    useTp: true, tpPerc: '0.4', useSl: true, slPerc: '0.6', baseSlOn: 'avg',
    moveSL: true, moveSLTrigger: '0.5', moveSLValue: '0',
    useSmartOrders: false, hodlDay: '', hodlAt: '', hodlNextBuy: 0,
    maxNumberOfOpenDeals: '5',
    profitCurrency: 'quote', orderFixedIn: 'quote',
    indicators: [
      { uuid: uuidv4(), type: IndicatorEnum.rsi, indicatorLength: 9, indicatorValue: '30',
        indicatorCondition: IndicatorStartConditionEnum.lt, indicatorInterval: ExchangeIntervals.fiveM,
        indicatorAction: IndicatorAction.startDeal, groupId: gid },
      { uuid: uuidv4(), type: IndicatorEnum.ma, maType: MAEnum.ema, indicatorLength: 20,
        indicatorValue: 'crossing', maCrossingValue: MAEnum.ema, maCrossingLength: 50,
        maCrossingInterval: ExchangeIntervals.fifteenM, maUUID: uuidv4(),
        indicatorCondition: IndicatorStartConditionEnum.gt, indicatorInterval: ExchangeIntervals.fifteenM,
        indicatorAction: IndicatorAction.startDeal, groupId: gid },
    ],
    indicatorGroups: [{ id: gid, logic: IndicatorsLogicEnum.and, action: IndicatorAction.startDeal, section: IndicatorSection.controller }],
  },
} as any)

bt.test([
  { interval: ExchangeIntervals.fiveM, bar: data5m },
  { interval: ExchangeIntervals.fifteenM, bar: data15m },
] as any).then((r: any) => {
  console.log('Result top keys:', Object.keys(r))
  console.log('\nfinancial fields:')
  for (const [k, v] of Object.entries(r.financial)) {
    if (typeof v === 'number' || typeof v === 'string') {
      console.log(`  ${k}: ${v}`)
    } else {
      console.log(`  ${k}: ${typeof v}`)
    }
  }
  console.log(`\ndeals: ${r.deals.length}`)
  if (r.deals.length > 0) {
    console.log('First deal fields:', Object.keys(r.deals[0]))
    const d = r.deals[0]
    console.log(`  id: ${d.id}`)
    console.log(`  filledOrders: ${d.filledOrders?.length}`)
    console.log(`  closedAt: ${d.closedAt} (${d.closedAt ? new Date(d.closedAt).toISOString() : 'still open'})`)
    console.log(`  finalProfit?:`, d.finalProfit, d.profit, d.realizedProfit)
    console.log(`  status?:`, d.status)
    console.log(`  closeReason?:`, d.closeReason)
    if (d.filledOrders) {
      console.log(`  first order:`, JSON.stringify(d.filledOrders[0]).slice(0,200))
      console.log(`  last order:`, JSON.stringify(d.filledOrders[d.filledOrders.length-1]).slice(0,200))
    }
  }
  console.log('\nprofits:', r.profits?.length)
  if (r.profits?.length) {
    console.log('  sample:', JSON.stringify(r.profits[0]).slice(0,200))
  }
  console.log('\nnumerical:', r.numerical ? Object.keys(r.numerical) : 'none')
  if (r.numerical) {
    for (const [k, v] of Object.entries(r.numerical)) {
      if (typeof v === 'number') console.log(`  ${k}: ${v}`)
    }
  }
}).catch((e: any) => { console.error(e.message); console.error(e.stack?.split('\n').slice(0,5).join('\n')) })
