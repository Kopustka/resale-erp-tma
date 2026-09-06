/**
 * Валюта отображения.
 *
 * У склада есть базовая валюта: в ней сервер хранит сведённые суммы и считает
 * аналитику. Менять её нельзя каждый раз, когда хочется прикинуть выручку в
 * долларах, — пересчитались бы все прошлые операции.
 *
 * Поэтому валюта отображения отдельная и живёт только на клиенте: она ничего
 * не меняет в данных, а лишь переводит уже посчитанные суммы при выводе.
 * Переключение мгновенное — состояние реактивное, и все суммы на экране
 * пересчитываются без единого запроса к серверу.
 *
 * Курсы приходят с бэкенда в виде «сколько базовой валюты стоит одна единица
 * валюты», поэтому обратный перевод — деление.
 */
import { reactive } from 'vue'
import { CURRENCY_SYMBOLS, type Currency } from '@/shared/api/types'

const LS_KEY = 'resale.displayCurrency'

interface FxState {
  base: Currency
  display: Currency
  /** rates[C] — сколько единиц base стоит 1 единица C. */
  rates: Record<string, number>
}

export const fx = reactive<FxState>({
  base: 'BYN',
  display: 'BYN',
  rates: {},
})

/** Базовая валюта склада. Валюту отображения не трогаем, если её выбирали. */
export function setBase(code: Currency): void {
  fx.base = code
  const saved = localStorage.getItem(LS_KEY) as Currency | null
  fx.display = saved && saved in CURRENCY_SYMBOLS ? saved : code
}

export function setRates(rates: Record<string, number>): void {
  fx.rates = rates
}

export function setDisplay(code: Currency): void {
  fx.display = code
  if (code === fx.base) localStorage.removeItem(LS_KEY)
  else localStorage.setItem(LS_KEY, code)
}

/** Показываем ли сейчас не в базовой валюте склада. */
export function isConverted(): boolean {
  return fx.display !== fx.base
}

/**
 * Сумма из базовой валюты в валюту отображения.
 *
 * Курса нет — возвращаем как есть. Показать неверную цифру хуже, чем
 * показать её в другой валюте: подпись рядом всё равно назовёт валюту.
 */
export function fromBase(value: number): number {
  if (fx.display === fx.base) return value
  const rate = fx.rates[fx.display]
  if (!rate || rate <= 0) return value
  return value / rate
}

/**
 * Сколько знаков после запятой показывать.
 *
 * В рублях и белорусских суммы крупные, копейки только шумят. В долларах и
 * евро та же вещь стоит в тридцать раз меньше, и без дробной части «15 Br»
 * превращается в «5 $» — разница между 4,60 и 5,40 теряется.
 */
export function fractionDigits(value: number, code: Currency): number {
  if (code === 'BYN' || code === 'RUB') return 0
  const abs = Math.abs(value)
  if (abs >= 1000) return 0
  return abs >= 100 ? 1 : 2
}

export function displaySymbol(): string {
  return CURRENCY_SYMBOLS[fx.display]
}
