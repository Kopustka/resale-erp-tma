import type { ItemStatus } from '@/shared/api/types'

/** Русские подписи статусов. */
export const STATUS_LABELS: Record<ItemStatus, string> = {
  BOUGHT: 'Куплен',
  PREPARING: 'Подготовка',
  PHOTOGRAPHED: 'Сфотографирован',
  LISTED: 'Выставлен',
  BOOKED: 'Забронирован',
  SOLD: 'Продан',
  SHIPPED: 'Отправлен',
  COMPLETED: 'Завершён',
  CANCELLED: 'Отменён',
  RETURNED: 'Возврат',
}

/**
 * Акцентные цвета статусов (fallback hex допустим по ТЗ для статусных акцентов).
 * Значения приглушённые, чтобы держаться плоского минимализма.
 */
export const STATUS_COLORS: Record<ItemStatus, string> = {
  BOUGHT: '#8e8e93',
  PREPARING: '#ff9500',
  PHOTOGRAPHED: '#5ac8fa',
  LISTED: '#2481cc',
  BOOKED: '#af52de',
  SOLD: '#34c759',
  SHIPPED: '#30b0c7',
  COMPLETED: '#248a3d',
  CANCELLED: '#ff3b30',
  RETURNED: '#ff9500',
}

/**
 * Следующий статус по happy-path (свайп вправо). Должен совпадать с
 * NEXT_STATUS на бэкенде (services/fsm.py).
 */
export const NEXT_STATUS: Partial<Record<ItemStatus, ItemStatus>> = {
  BOUGHT: 'PREPARING',
  PREPARING: 'PHOTOGRAPHED',
  PHOTOGRAPHED: 'LISTED',
  LISTED: 'BOOKED',
  BOOKED: 'SOLD',
  SOLD: 'SHIPPED',
  SHIPPED: 'COMPLETED',
}

export function nextStatus(status: ItemStatus): ItemStatus | null {
  return NEXT_STATUS[status] ?? null
}

/**
 * Разрешённые переходы (зеркало ALLOWED_TRANSITIONS на бэкенде, services/fsm.py).
 * Используется в детали товара для ручной смены статуса.
 */
export const ALLOWED_TRANSITIONS: Record<ItemStatus, ItemStatus[]> = {
  BOUGHT: ['PREPARING', 'PHOTOGRAPHED', 'CANCELLED'],
  PREPARING: ['PHOTOGRAPHED', 'CANCELLED', 'BOUGHT'],
  PHOTOGRAPHED: ['LISTED', 'CANCELLED', 'PREPARING'],
  LISTED: ['BOOKED', 'SOLD', 'CANCELLED', 'PHOTOGRAPHED'],
  BOOKED: ['SOLD', 'LISTED', 'CANCELLED'],
  SOLD: ['SHIPPED', 'RETURNED', 'CANCELLED', 'BOOKED'],
  SHIPPED: ['COMPLETED', 'RETURNED', 'SOLD'],
  COMPLETED: ['RETURNED', 'SHIPPED'],
  RETURNED: ['LISTED', 'PREPARING'],
  CANCELLED: ['LISTED'],
}

export function allowedTransitions(status: ItemStatus): ItemStatus[] {
  return ALLOWED_TRANSITIONS[status] ?? []
}

/** Откат на шаг назад по happy path (зеркало PREV_STATUS на бэкенде). */
export const PREV_STATUS: Partial<Record<ItemStatus, ItemStatus>> = {
  PREPARING: 'BOUGHT',
  PHOTOGRAPHED: 'PREPARING',
  LISTED: 'PHOTOGRAPHED',
  BOOKED: 'LISTED',
  SOLD: 'BOOKED',
  SHIPPED: 'SOLD',
  COMPLETED: 'SHIPPED',
}

export function prevStatus(status: ItemStatus): ItemStatus | null {
  return PREV_STATUS[status] ?? null
}

/** Требует ли переход в этот статус ввод selling_price. */
export function requiresSellingPrice(target: ItemStatus): boolean {
  return target === 'SOLD'
}

/** Список всех статусов для фильтра. */
export const ALL_STATUSES: ItemStatus[] = [
  'BOUGHT',
  'PREPARING',
  'PHOTOGRAPHED',
  'LISTED',
  'BOOKED',
  'SOLD',
  'SHIPPED',
  'COMPLETED',
  'CANCELLED',
  'RETURNED',
]

/** Статусы, где цена продажи считается «зелёной» (реализовано). */
export const SOLD_LIKE: ItemStatus[] = ['SOLD', 'SHIPPED', 'COMPLETED']

export function isSoldLike(status: ItemStatus): boolean {
  return SOLD_LIKE.includes(status)
}
