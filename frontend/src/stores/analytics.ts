import { defineStore } from 'pinia'
import { analyticsApi } from '@/shared/api/endpoints'
import type { AnalyticsSummary } from '@/shared/api/types'

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
    async fetch(): Promise<void> {
      this.loading = true
      this.error = null
      try {
        this.summary = await analyticsApi.summary()
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Не удалось загрузить аналитику'
      } finally {
        this.loading = false
      }
    },
  },
})
