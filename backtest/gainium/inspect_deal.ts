// Look at actual deal outcomes — what closed them, what was P/L

import DCABacktesting from './src/dca'
import {
  ExchangeIntervals, ExchangeEnum, StrategyEnum,
  OrderTypeEnum, StartConditionEnum, OrderSizeTypeEnum,
  IndicatorEnum, IndicatorStartConditionEnum, IndicatorsLogicEnum,
  IndicatorAction, IndicatorSection, MAEnum, CloseConditionEnum,
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
const data5m = bars.filter(b => b.time >= bars[bars.length-1].time - 30 * 86400 * 1000)
const data15m: any[] = []
let bk: any = null
for (const b of data5m) {
  const t = Math.floor(b.time / 900000) * 900000
  if (!bk || bk.time !== t) { if (bk) data15m.push(bk); bk = { time: t, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume, symbol: 'BTCUSDT', isFinal: true } }
  else { bk.high = Math.max(bk.high, b.high); bk.low = Math.min(bk.low, b.low); bk.close = b.close; bk.volume += b.volume }
}
if (bk) data15m.push(bk)

const gid = uuidv4()
const bt: any = new DCABacktesting({
  exchange: ExchangeEnum.bybit,
  symbols: [{ pair: 'BTCUSDT', exchange: ExchangeEnum.bybit,
    baseAsset: { minAmount: 0.001, maxAmount: 1500, step: 0.001, name: 'BTC' },
    quoteAsset: { minAmount: 5, name: 'USDT' }, maxOrders: 200, priceAssetPrecision: 1 }],
  interval: ExchangeIntervals.fiveM,
  balances: [{ asset: 'USDT', free: '5000', locked: '0' }],
  userFee: 0.055, slippage: 0.02,
  prices: [{ symbol: 'BTCUSDT', price: data5m[data5m.length-1].close }],
  settings: {
    pair: ['BTCUSDT'], name: 'inspect', strategy: StrategyEnum.long,
    baseOrderSize: '100', orderSize: '100', orderSizeType: OrderSizeTypeEnum.usd,
    startOrderType: OrderTypeEnum.market, startCondition: StartConditionEnum.ti,
    useDca: false, ordersCount: '1', activeOrdersCount: '1', step: '0.5', stepScale: '1', volumeScale: '1',
    useTp: true, tpPerc: '0.4', dealCloseCondition: CloseConditionEnum.tp,
    useSl: true, slPerc: '0.6', baseSlOn: 'avg',
    useSmartOrders: false, hodlDay: '', hodlAt: '', hodlNextBuy: 0,
    maxNumberOfOpenDeals: '5',
    profitCurrency: 'quote', orderFixedIn: 'quote',
    indicators: [{
      uuid: uuidv4(), type: IndicatorEnum.rsi, indicatorLength: 9, indicatorValue: '30',
      indicatorCondition: IndicatorStartConditionEnum.lt, indicatorInterval: ExchangeIntervals.fiveM,
      indicatorAction: IndicatorAction.startDeal, groupId: gid,
    }],
    indicatorGroups: [{ id: gid, logic: IndicatorsLogicEnum.and, action: IndicatorAction.startDeal, section: IndicatorSection.controller }],
  },
} as any)

bt.test([
  { interval: ExchangeIntervals.fiveM, bar: data5m },
  { interval: ExchangeIntervals.fifteenM, bar: data15m },
] as any).then((r: any) => {
  console.log(`\nTotal: ${r.deals.length} deals`)
  console.log(`Closed: ${r.numerical.closed}, Open: ${r.numerical.open}`)
  console.log(`Win: ${r.numerical.profit}, Loss: ${r.numerical.loss}`)
  console.log(`Net profit USD: $${r.financial.netProfitTotalUsd?.toFixed(2)}`)
  console.log()
  for (let i = 0; i < Math.min(3, r.deals.length); i++) {
    const d = r.deals[i]
    console.log(`── Deal ${i+1} ──`)
    console.log(`  status: ${d.status}`)
    console.log(`  startTime: ${new Date(d.startTime).toISOString()}`)
    console.log(`  closedTime: ${d.closedTime ? new Date(d.closedTime).toISOString() : 'OPEN'}`)
    console.log(`  startPrice: ${d.startPrice}`)
    console.log(`  avgPrice: ${d.avgPrice}`)
    console.log(`  closePrice: ${d.closePrice}`)
    console.log(`  profit:`, d.profit)
    console.log(`  filledOrders:`)
    if (d.filledOrders) for (const o of d.filledOrders) console.log(`    ${o.side} @ ${o.price} on ${new Date(o.filledTime).toISOString()}`)
    console.log()
  }
}).catch((e: any) => { console.error(e.message) })
