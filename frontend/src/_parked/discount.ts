/**
 * Расчёт скидки. Должен совпадать с сервером (services/discounts.py):
 * округление ВНИЗ, шаг зависит от валюты.
 *
 * Дублирование намеренное: кнопка «−30%» обязана показать ровно ту цену,
 * которую запишет сервер, иначе пользователь увидит одно, а сохранится
 * другое. При правке менять оба места.
 */
import type { Currency } from '@/shared/api/types'

export const QUICK_PERCENTS = [10, 20, 30] as const

/**
 * Шаг округления. В рублях РФ ценник с единицами выглядит неопрятно
 * («1547»), поэтому округляем до десятков. В остальных валютах суммы
 * на порядок меньше — достаточно убрать копейки.
 */
const ROUND_STEPS: Record<string, number> = { RUB: 10, BYN: 1, USD: 1, EUR: 1 }
const DEFAULT_STEP = 1

export function stepFor(currency?: Currency | string | null): number {
  return ROUND_STEPS[(currency ?? '').toUpperCase()] ?? DEFAULT_STEP
}

/** Округление вниз до шага валюты. */
export function roundPrice(value: number, currency?: Currency | string | null): number {
  const step = stepFor(currency)
  const stepped = Math.floor(value / step) * step
  // Дешёвая вещь при крупном шаге дала бы ноль и упёрлась в проверку
  // «цена больше нуля». Для такого вырожденного случая режем до целых.
  if (stepped <= 0 && value > 0) return Math.floor(value)
  return stepped
}

export function applyPercent(
  price: number,
  percent: number,
  currency?: Currency | string | null,
): number {
  return roundPrice((price * (100 - percent)) / 100, currency)
}

export function percentOf(oldPrice: number, newPrice: number): number {
  if (!oldPrice || oldPrice <= 0) return 0
  return Math.round((1 - newPrice / oldPrice) * 100)
}
