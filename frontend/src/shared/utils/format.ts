/** Форматирование денег, процентов, дат. Мультивалюта; базовая — из настроек склада. */
import { CURRENCY_SYMBOLS, type Currency } from '@/shared/api/types'

// Базовая валюта склада (устанавливается session-стором при загрузке).
let baseCurrency: Currency = 'BYN'
export function setBaseCurrency(code: Currency): void {
  baseCurrency = code
}
export function getBaseCurrency(): Currency {
  return baseCurrency
}
export function moneySymbol(code?: Currency | null): string {
  return CURRENCY_SYMBOLS[(code ?? baseCurrency) as Currency] ?? String(code ?? '')
}
/** Символ базовой валюты (для меток «в базе»). */
export function baseSymbol(): string {
  return CURRENCY_SYMBOLS[baseCurrency]
}

const moneyFmt = new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 0,
})

const moneyFmtSigned = new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 0,
  signDisplay: 'always',
})

/** «12 500 Br». null -> «—». cur не указан -> базовая валюта. */
export function formatMoney(value: number | null | undefined, cur?: Currency | null): string {
  if (value === null || value === undefined) return '—'
  return `${moneyFmt.format(Math.round(value))} ${moneySymbol(cur)}`
}

/** «+12 500 Br» / «-800 Br» — для прибыли (в базовой валюте). */
export function formatMoneySigned(value: number | null | undefined, cur?: Currency | null): string {
  if (value === null || value === undefined) return '—'
  return `${moneyFmtSigned.format(Math.round(value))} ${moneySymbol(cur)}`
}

/** «34%». null -> «—». */
export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value)}%`
}

/** Дата в компактном виде «30 июл». */
const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' })

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return dateFmt.format(d)
}

/** Сколько дней прошло с даты (для «зависших» товаров). */
export function daysSince(iso: string | null | undefined): number | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const diff = Date.now() - d.getTime()
  return Math.floor(diff / 86_400_000)
}

/** Округлённое число дней «12 дн.». */
export function formatDays(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value)} дн.`
}
