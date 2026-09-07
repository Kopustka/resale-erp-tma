import { defineStore } from 'pinia'

export interface ToastItem {
  id: number
  message: string
  kind: 'info' | 'success' | 'error'
  actionLabel?: string
  onAction?: () => void
  duration: number
  /** Сколько раз повторилось одно и то же сообщение. */
  count: number
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
    /**
     * Показать сообщение.
     *
     * Одинаковые подряд не множим, а считаем. Быстрые нажатия на кнопку
     * этапа упирались в одну и ту же проверку, и экран заваливало пятью
     * одинаковыми плашками — прочесть их было невозможно, а закрывать
     * приходилось каждую.
     */
    show(opts: ShowOptions): number {
      const same = this.toasts.find(
        (t) => t.message === opts.message && t.kind === (opts.kind ?? 'info'),
      )
      if (same && !opts.onAction) {
        same.count += 1
        return same.id
      }
      const id = ++seq
      this.toasts.push({
        id,
        message: opts.message,
        kind: opts.kind ?? 'info',
        actionLabel: opts.actionLabel,
        onAction: opts.onAction,
        duration: opts.duration ?? 3000,
        count: 1,
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
