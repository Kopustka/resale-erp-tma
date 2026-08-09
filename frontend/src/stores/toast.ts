import { defineStore } from 'pinia'

export interface ToastItem {
  id: number
  message: string
  kind: 'info' | 'success' | 'error'
  actionLabel?: string
  onAction?: () => void
  duration: number
}

interface ShowOptions {
  message: string
  kind?: ToastItem['kind']
  actionLabel?: string
  onAction?: () => void
  duration?: number
}

let seq = 0

export const useToastStore = defineStore('toast', {
  state: () => ({
    toasts: [] as ToastItem[],
  }),
  actions: {
    show(opts: ShowOptions): number {
      const id = ++seq
      this.toasts.push({
        id,
        message: opts.message,
        kind: opts.kind ?? 'info',
        actionLabel: opts.actionLabel,
        onAction: opts.onAction,
        duration: opts.duration ?? 3000,
      })
      return id
    },
    success(message: string): number {
      return this.show({ message, kind: 'success' })
    },
    error(message: string): number {
      return this.show({ message, kind: 'error', duration: 4000 })
    },
    dismiss(id: number): void {
      const idx = this.toasts.findIndex((t) => t.id === id)
      if (idx !== -1) this.toasts.splice(idx, 1)
    },
    /** Пользователь нажал action у тоста. */
    trigger(id: number): void {
      const t = this.toasts.find((x) => x.id === id)
      if (t?.onAction) t.onAction()
      this.dismiss(id)
    },
  },
})
