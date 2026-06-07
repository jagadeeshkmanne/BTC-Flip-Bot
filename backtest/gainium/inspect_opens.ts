import DCABacktesting from './src/dca'
import { ExchangeIntervals, ExchangeEnum, StrategyEnum, OrderTypeEnum, StartConditionEnum, OrderSizeTypeEnum, IndicatorEnum, IndicatorStartConditionEnum, IndicatorsLogicEnum, IndicatorAction, IndicatorSection, MAEnum, CloseConditionEnum } from './src/types'
import * as fs from 'fs'
import { v4 as uuidv4 } from 'uuid'

const CSV = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
const lines = fs.readFileSync(CSV, 'utf-8').trim().split('\n')
const bars: any[] = []
for (let i = 1; i < lines.length; i++) {
  const c = lines[i].split(',')
  bars.push({ time: Math.floor(new Date(c[0]).getTime() / 1000) * 1000, open: +c[1], high: +c[2], low: +c[3], close: +c[4], volume: +c[5], symbol: 'BTCUSDT', isFinal: true })
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

const PER_LEG = 2375
const gid = uuidv4()
const bt: any = new DCABacktesting({
  exchange: ExchangeEnum.bybitUsdm,
  symbols: [{ pair: 'BTCUSDT', exchange: ExchangeEnum.bybitUsdm,
    baseAsset: { minAmount: 0.001, maxAmount: 1500, step: 0.001, name: 'BTC' },
    quoteAsset: { minAmount: 5, name: 'USDT' }, maxOrders: 200, priceAssetPrecision: 1 }],
  interval: ExchangeIntervals.fiveM,
  balances: [{ asset: 'USDT', free: '5000', locked: '0' }],
  userFee: 0.0004, slippage: 0.0002,
  prices: [{ symbol: 'BTCUSDT', price: data5m[data5m.length-1].close }],
  settings: {
    pair: ['BTCUSDT'], name: 'v1 LONG open inspect', strategy: StrategyEnum.long,
    baseOrderSize: String(PER_LEG), orderSize: String(PER_LEG), orderSizeType: OrderSizeTypeEnum.usd,
    startOrderType: OrderTypeEnum.market, useLimitPrice: false,
    startCondition: StartConditionEnum.ti,
    useDca: true, ordersCount: '2', activeOrdersCount: '2', step: '0.5', stepScale: '1', volumeScale: '1',
    useTp: true, tpPerc: '0.5', dealCloseCondition: CloseConditionEnum.tp,
    useSl: true, slPerc: '0.6', baseSlOn: 'start',
    moveSL: true, moveSLTrigger: '0.5', moveSLValue: '0',
    futures: true, leverage: 3,
    maxNumberOfOpenDeals: '1',
    profitCurrency: 'quote', orderFixedIn: 'quote',
    cooldownAfterDealStart: false, cooldownAfterDealStop: false,
    useSmartOrders: false, hodlDay: '', hodlAt: '', hodlNextBuy: 0,
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
  fullResult: true,
} as any)

bt.test([{ interval: ExchangeIntervals.fiveM, bar: data5m }, { interval: ExchangeIntervals.fifteenM, bar: data15m }] as any).then((r: any) => {
  const deals = r.deals as any[]
  console.log(`\nTotal raw deals: ${deals.length}`)
  console.log(`Status breakdown:`)
  const byStatus = new Map<string, number>()
  for (const d of deals) byStatus.set(d.status, (byStatus.get(d.status) ?? 0) + 1)
  for (const [s, n] of byStatus) console.log(`  ${s}: ${n}`)
  
  console.log(`\n── ALL DEALS DETAIL ──`)
  for (let i = 0; i < deals.length; i++) {
    const d = deals[i]
    const lastPrice = data5m[data5m.length - 1].close
    let unrealized = 0
    if (d.status === 'open' && d.avgPrice && d.filledOrders) {
      const qty = d.filledOrders.reduce((acc: number, o: any) => acc + (o.qty || 0), 0)
      unrealized = (lastPrice - d.avgPrice) * qty - lastPrice * qty * 0.0004 * 2
    }
    console.log(`  #${i+1} ${d.status} | start ${new Date(d.startTime).toISOString().slice(0,16)} | entries ${d.filledOrders?.length} | avg $${d.avgPrice?.toFixed(2)} | status: ${d.status} | profit $${(d.profit?.totalUsd ?? 0).toFixed(2)}${d.status === 'open' ? ` | UNREALIZED at last bar $${unrealized.toFixed(2)}` : ''}`)
  }
})

// Find the lowest price after position #1 opened
