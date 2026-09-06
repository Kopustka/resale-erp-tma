import type { ItemStatus } from '@/shared/api/types'

/** Русские подписи статусов. */
export const STATUS_LABELS: Record<ItemStatus, string> = {
  BOUGHT: 'Куплен',
  PREPARING: 'Подготовка',
  // «Сфотографирован» — пятнадцать символов, они не влезали ни в чип, ни в
  // кнопку перехода и всюду обрезались многоточием.
  PHOTOGRAPHED: 'Отснято',
  LISTED: 'Выставлен',
  SHIPPED: 'Отправлен',
}

/**
 * Акцентные цвета статусов (fallback hex допустим по ТЗ для статусных акцентов).
 * Значения приглушённые, чтобы держаться плоского минимализма.
 */
export const STATUS_COLORS: Record<ItemStatus, string> = {
  BOUGHT: '#8A9099',
  PREPARING: '#F0A02A',
  PHOTOGRAPHED: '#4CC2FF',
  LISTED: '#4C6FFF',
  SHIPPED: '#3FCF8E',
}

/**
 * Следующий статус по happy-path (кнопка в строке списка). Должен совпадать с
 * NEXT_STATUS на бэкенде (services/fsm.py).
 */
export const NEXT_STATUS: Partial<Record<ItemStatus, ItemStatus>> = {
  BOUGHT: 'PREPARING',
  PREPARING: 'PHOTOGRAPHED',
  PHOTOGRAPHED: 'LISTED',
  LISTED: 'SHIPPED',
}

export function nextStatus(status: ItemStatus): ItemStatus | null {
  return NEXT_STATUS[status] ?? null
}

/**
 * Разрешённые переходы (зеркало ALLOWED_TRANSITIONS на бэкенде, services/fsm.py).
 * Используется в детали товара для ручной смены статуса.
 */
export const ALLOWED_TRANSITIONS: Record<ItemStatus, ItemStatus[]> = {
  BOUGHT: ['PREPARING', 'PHOTOGRAPHED'],
  PREPARING: ['PHOTOGRAPHED', 'BOUGHT'],
  PHOTOGRAPHED: ['LISTED', 'PREPARING'],
  LISTED: ['SHIPPED', 'PHOTOGRAPHED'],
  SHIPPED: ['LISTED'],
}

export function allowedTransitions(status: ItemStatus): ItemStatus[] {
  return ALLOWED_TRANSITIONS[status] ?? []
}

/** Откат на шаг назад по happy path (зеркало PREV_STATUS на бэкенде). */
export const PREV_STATUS: Partial<Record<ItemStatus, ItemStatus>> = {
  PREPARING: 'BOUGHT',
  PHOTOGRAPHED: 'PREPARING',
  LISTED: 'PHOTOGRAPHED',
  SHIPPED: 'LISTED',
}

export function prevStatus(status: ItemStatus): ItemStatus | null {
  return PREV_STATUS[status] ?? null
}

/**
 * Требует ли переход в этот статус ввод цены продажи.
 * «Отправлен» — теперь единственное состояние проданной вещи.
 */
export function requiresSellingPrice(target: ItemStatus): boolean {
  return target === 'SHIPPED'
}

/**
 * Требует ли переход указанной цены в объявлении.
 * В канал не выпускаем вещь без ценника — пост без суммы бесполезен.
 */
export function requiresListPrice(target: ItemStatus): boolean {
  return target === 'LISTED'
}

/** Список всех статусов для фильтра. */
export const ALL_STATUSES: ItemStatus[] = [
  'BOUGHT',
  'PREPARING',
  'PHOTOGRAPHED',
  'LISTED',
  'SHIPPED',
]

/** Статусы, где цена продажи считается «зелёной» (реализовано). */
export const SOLD_LIKE: ItemStatus[] = ['SHIPPED']

export function isSoldLike(status: ItemStatus): boolean {
  return SOLD_LIKE.includes(status)
}

/**
 * Имя CSS-переменной этапа. Компонент подставляет `var(--s-<vars>)` для точки
 * и полосы и `var(--s-<vars>-ink)` для надписи: на своей же заливке цвет
 * этапа не всегда читается, особенно в светлой теме.
 */
export const STATUS_VARS: Record<ItemStatus, string> = {
  BOUGHT: 'bought',
  PREPARING: 'prep',
  PHOTOGRAPHED: 'photo',
  LISTED: 'listed',
  SHIPPED: 'ship',
}

/**
 * Подпись кнопки перехода. Кнопка обещает действие, а не называет состояние:
 * «Выставить» вместо «Выставлен». Разные слова для одного и того же этапа —
 * это нормально: на бейдже он свершившийся факт, на кнопке ещё намерение.
 */
export const ACTION_LABELS: Record<ItemStatus, string> = {
  BOUGHT: 'Вернуть',
  PREPARING: 'В подготовку',
  PHOTOGRAPHED: 'Отснять',
  LISTED: 'Выставить',
  SHIPPED: 'Отправить',
}
