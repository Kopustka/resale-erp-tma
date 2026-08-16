/**
 * Расчёт скидки. Должен совпадать с сервером (services/discounts.py):
 * округление ВНИЗ до целых. Вниз, а не к ближайшему, иначе покупатель
 * получил бы скидку меньше обещанной и «−30%» оказалось бы неправдой.
 */
export const QUICK_PERCENTS = [10, 20, 30] as const

/** Шаг округления. Поставить 10, если нужны круглые ценники 100/150/200. */
const ROUND_TO = 1

export function roundPrice(value: number): number {
  return Math.floor(value / ROUND_TO) * ROUND_TO
}

export function applyPercent(price: number, percent: number): number {
  return roundPrice((price * (100 - percent)) / 100)
}

export function percentOf(oldPrice: number, newPrice: number): number {
  if (!oldPrice || oldPrice <= 0) return 0
  return Math.round((1 - newPrice / oldPrice) * 100)
}
