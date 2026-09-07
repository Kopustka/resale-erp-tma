import { defineStore } from 'pinia'
import { analyticsApi } from '@/shared/api/endpoints'
import type { AnalyticsSummary } from '@/shared/api/types'

/**
 * Запрос сводки идёт из трёх мест: список тянет её для шапки, аналитика —
 * при открытии вкладки и по таймеру. Каждый вызывал свой запрос, и в
 * журнале сервера набегало по десятку одинаковых обращений за полминуты.
 *
 * Держим текущий запрос здесь: пока он в полёте, все получают его же.
 * Плюс не чаще раза в пять секунд — сводка меняется медленно, а лишние
 * запросы на мобильной сети это заметная задержка и трафик.
 */
let inFlight: Promise<void> | null = null
let lastAt = 0
const MIN_GAP_MS = 5000

interface AnalyticsState {
  summary: AnalyticsSummary | null
  loading: boolean
  error: string | null
}

export const useAnalyticsStore = defineStore('analytics', {
  state: (): AnalyticsState => ({
    summary: null,
    loading: false,
    error: null,
  }),

  actions: {
    /** force — обновить, даже если только что обновляли (кнопка «Обновить»). */
    async fetch(force = false): Promise<void> {
      if (inFlight) return inFlight
      const now = Date.now()
      if (!force && this.summary !== null && now - lastAt < MIN_GAP_MS) return

      this.loading = true
      this.error = null
      inFlight = (async () => {
        try {
          this.summary = await analyticsApi.summary()
          lastAt = Date.now()
        } catch (e) {
          this.error = e instanceof Error ? e.message : 'Не удалось загрузить аналитику'
        } finally {
          this.loading = false
          inFlight = null
        }
      })()
      return inFlight
    },
  },
})
