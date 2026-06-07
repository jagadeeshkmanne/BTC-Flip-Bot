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

async function run(rsiOversold: number, rsiOverbought: number, dcaLevels: string, direction: 'LONG' | 'SHORT') {
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
      pair: ['BTCUSDT'], name: 'strict', strategy: direction === 'LONG' ? StrategyEnum.long : StrategyEnum.short,
      baseOrderSize: '2375', orderSize: '2375', orderSizeType: OrderSizeTypeEnum.usd,
      startOrderType: OrderTypeEnum.market, useLimitPrice: false,
      startCondition: StartConditionEnum.ti,
      useDca: dcaLevels !== '1', ordersCount: dcaLevels, activeOrdersCount: dcaLevels,
      step: '0.5', stepScale: '1', volumeScale: '1',
      useTp: true, tpPerc: '0.5', dealCloseCondition: CloseConditionEnum.tp,
      useSl: true, slPerc: '0.6', baseSlOn: 'start',
      moveSL: true, moveSLTrigger: '0.5', moveSLValue: '0',
      futures: true, leverage: 3,
      maxNumberOfOpenDeals: '1',
      profitCurrency: 'quote', orderFixedIn: 'quote',
      cooldownAfterDealStart: false, cooldownAfterDealStop: false,
      useSmartOrders: false, hodlDay: '', hodlAt: '', hodlNextBuy: 0,
      indicators: [
        { uuid: uuidv4(), type: IndicatorEnum.rsi, indicatorLength: 9,
          indicatorValue: direction === 'LONG' ? String(rsiOversold) : String(rsiOverbought),
          indicatorCondition: direction === 'LONG' ? IndicatorStartConditionEnum.lt : IndicatorStartConditionEnum.gt,
          indicatorInterval: ExchangeIntervals.fiveM, indicatorAction: IndicatorAction.startDeal, groupId: gid },
        { uuid: uuidv4(), type: IndicatorEnum.ma, maType: MAEnum.ema, indicatorLength: 20,
          indicatorValue: 'crossing', maCrossingValue: MAEnum.ema, maCrossingLength: 50,
          maCrossingInterval: ExchangeIntervals.fifteenM, maUUID: uuidv4(),
          indicatorCondition: direction === 'LONG' ? IndicatorStartConditionEnum.gt : IndicatorStartConditionEnum.lt,
          indicatorInterval: ExchangeIntervals.fifteenM, indicatorAction: IndicatorAction.startDeal, groupId: gid },
      ],
      indicatorGroups: [{ id: gid, logic: IndicatorsLogicEnum.and, action: IndicatorAction.startDeal, section: IndicatorSection.controller }],
    },
    fullResult: true,
  } as any)
  return await bt.test([{ interval: ExchangeIntervals.fiveM, bar: data5m }, { interval: ExchangeIntervals.fifteenM, bar: data15m }] as any)
}

const lastPrice = data5m[data5m.length-1].close
function stats(r: any, label: string) {
  const deals = r.deals as any[]
  let closed = 0, wins = 0, losses = 0, totalProfit = 0, unrealized = 0, dca = 0
  for (const d of deals) {
    if (d.status === 'closed') {
      closed++
      const p = d.profit?.totalUsd ?? 0
      totalProfit += p
      if (p > 0) wins++; else if (p < 0) losses++
      if (d.filledOrders?.length >= 2) dca++
    } else if (d.status === 'open' && d.avgPrice && d.filledOrders) {
      const qty = d.filledOrders.reduce((a: number, o: any) => a + (o.qty || 0), 0)
      const side = d.settings?.strategy === 'LONG' ? 1 : -1
      const mtm = (lastPrice - d.avgPrice) * qty * side - lastPrice * qty * 0.0004 * 2
      unrealized += mtm
    }
  }
  console.log(`${label.padEnd(40)} | ${closed}W/${losses}L closed | ${dca}/${closed} hit DCA | $${totalProfit.toFixed(0)} realized | ${unrealized < 0 ? '$'+unrealized.toFixed(0)+' unreal' : 'no stuck'}`)
}

(async () => {
  console.log('Testing alternatives (1mo, all configs use 0.5% TP, 0.6% SL, ATR filter not applied)\n')
  console.log('Config'.padEnd(40) + ' | Closed deals  | DCA hits   | Realized        | Unrealized')
  console.log('-'.repeat(120))
  // Current v1 config (RSI 30/70 + DCA)
  for (const dir of ['LONG', 'SHORT'] as const) {
    stats(await run(30, 70, '2', dir), `RSI 30/70 + DCA (${dir})`)
  }
  // No DCA — risky but bigger per-trade
  for (const dir of ['LONG', 'SHORT'] as const) {
    stats(await run(30, 70, '1', dir), `RSI 30/70 + NO DCA (${dir})`)
  }
  // Stricter RSI 25/75 + DCA
  for (const dir of ['LONG', 'SHORT'] as const) {
    stats(await run(25, 75, '2', dir), `RSI 25/75 + DCA (${dir})`)
  }
  // Stricter RSI 25/75 + NO DCA
  for (const dir of ['LONG', 'SHORT'] as const) {
    stats(await run(25, 75, '1', dir), `RSI 25/75 + NO DCA (${dir})`)
  }
})()
