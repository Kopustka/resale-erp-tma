/**
 * Лёгкая таб-навигация без vue-router: reactive activeTab + оверлей создания.
 * Плюс канал drill-down из BI в Склад (по ids).
 */
import { reactive, readonly } from 'vue'
import type { ItemStatus } from '@/shared/api/types'

export type Tab = 'inventory' | 'bi' | 'settings'
export type Overlay = 'create' | 'detail' | 'templates' | null

interface DrilldownPayload {
  ids?: string[]
  status?: ItemStatus | null
  brand?: string | null
}

interface NavState {
  activeTab: Tab
  overlay: Overlay
  /** id товара для оверлея детали/редактирования. */
  detailItemId: string | null
  /** Одноразовая полезная нагрузка для Склада (drill-down). Экран её «съедает». */
  inventoryDrilldown: DrilldownPayload | null
}

const state = reactive<NavState>({
  activeTab: 'inventory',
  overlay: null,
  detailItemId: null,
  inventoryDrilldown: null,
})

export const nav = readonly(state)

export function setTab(tab: Tab): void {
  state.activeTab = tab
}

export function openCreate(): void {
  state.overlay = 'create'
}

export function openDetail(itemId: string): void {
  state.detailItemId = itemId
  state.overlay = 'detail'
}

/** Оверлей «Шаблоны постов» (из настроек, только OWNER). */
export function openTemplates(): void {
  state.overlay = 'templates'
}

export function closeOverlay(): void {
  state.overlay = null
  state.detailItemId = null
}

/** Переход из BI на Склад с преднастроенным фильтром (drill-down). */
export function drilldownToInventory(payload: DrilldownPayload): void {
  state.inventoryDrilldown = payload
  state.activeTab = 'inventory'
}

/** Склад забирает и очищает drill-down. */
export function consumeDrilldown(): DrilldownPayload | null {
  const p = state.inventoryDrilldown
  state.inventoryDrilldown = null
  return p
}
