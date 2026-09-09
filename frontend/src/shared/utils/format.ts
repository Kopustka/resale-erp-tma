/** Форматирование денег, процентов, дат. Мультивалюта; базовая — из настроек склада. */
import { CURRENCY_SYMBOLS, type Currency } from '@/shared/api/types'
import { fractionDigits, fx } from './currency'

// Базовая валюта склада (устанавливается session-стором при загрузке).
let baseCurrency: Currency = 'BYN'
export function setBaseCurrency(code: Currency): void {
  baseCurrency = code
}
/**
 * Символ валюты суммы. Без явного кода это сумма склада, показанная в валюте
 * отображения, — значит и символ её, иначе подпись врала бы о цифре.
 */
export function moneySymbol(code?: Currency | null): string {
  return CURRENCY_SYMBOLS[(code ?? fx.display) as Currency] ?? String(code ?? '')
}
/** Символ базовой валюты (для меток «в базе»). */
export function baseSymbol(): string {
  return CURRENCY_SYMBOLS[baseCurrency]
}

/** Форматтеры кэшируем по числу знаков: пересоздавать их на каждую сумму дорого. */
const fmtCache = new Map<string, Intl.NumberFormat>()
function fmt(digits: number, signed: boolean): Intl.NumberFormat {
  const key = `${digits}:${signed}`
  let f = fmtCache.get(key)
  if (!f) {
    f = new Intl.NumberFormat('ru-RU', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
      ...(signed ? { signDisplay: 'always' as const } : {}),
    })
    fmtCache.set(key, f)
  }
  return f
}

function render(value: number, cur: Currency | null | undefined, signed: boolean): string {
  const code = (cur ?? fx.display) as Currency
  const digits = fractionDigits(value, code)
  return `${fmt(digits, signed).format(value)} ${moneySymbol(cur)}`
}

/** «12 500 Br». null -> «—». cur не указан -> валюта отображения. */
export function formatMoney(value: number | null | undefined, cur?: Currency | null): string {
  if (value === null || value === undefined) return '—'
  return render(value, cur, false)
}

/** «+12 500 Br» / «-800 Br» — для прибыли. */
export function formatMoneySigned(value: number | null | undefined, cur?: Currency | null): string {
  if (value === null || value === undefined) return '—'
  return render(value, cur, true)
}

/** «34%». null -> «—». */
export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value)}%`
}

/** Округлённое число дней «12 дн.». */
export function formatDays(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value)} дн.`
}
