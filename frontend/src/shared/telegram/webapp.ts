/**
 * Обёртка над window.Telegram.WebApp.
 *
 * - init(): ready() + expand() + применение темы, с dev-fallback вне Telegram.
 * - getInitData(): возвращает tg.initData либо VITE_DEV_INIT_DATA (dev-мок).
 * - haptic-* : безопасные врапперы (no-op вне Telegram).
 * - тема: маппит themeParams -> CSS-переменные --tg-theme-*. Вне Telegram
 *   ставит дефолты (light/dark по prefers-color-scheme), чтобы UI был виден.
 */

export function getWebApp(): TelegramWebApp | undefined {
  return window.Telegram?.WebApp
}

/** Признак реального запуска внутри Telegram (есть подписанный initData). */
export function isInTelegram(): boolean {
  const tg = getWebApp()
  return !!tg && typeof tg.initData === 'string' && tg.initData.length > 0
}

/**
 * initData для заголовка X-TG-Init-Data.
 * В реальном Telegram — подписанная строка. В dev-браузере — из env-мока.
 */
export function getInitData(): string {
  const tg = getWebApp()
  if (tg && tg.initData) return tg.initData
  return import.meta.env.VITE_DEV_INIT_DATA ?? ''
}

// --------------------------------- Тема --------------------------------- //

const CSS_VAR_BY_KEY: Record<string, string> = {
  bg_color: '--tg-theme-bg-color',
  text_color: '--tg-theme-text-color',
  hint_color: '--tg-theme-hint-color',
  link_color: '--tg-theme-link-color',
  button_color: '--tg-theme-button-color',
  button_text_color: '--tg-theme-button-text-color',
  secondary_bg_color: '--tg-theme-secondary-bg-color',
  header_bg_color: '--tg-theme-header-bg-color',
  accent_text_color: '--tg-theme-accent-text-color',
  section_bg_color: '--tg-theme-section-bg-color',
  destructive_text_color: '--tg-theme-destructive-text-color',
}

const DEV_THEME_LIGHT: TelegramThemeParams = {
  bg_color: '#ffffff',
  text_color: '#000000',
  hint_color: '#707579',
  link_color: '#2481cc',
  button_color: '#2481cc',
  button_text_color: '#ffffff',
  secondary_bg_color: '#f0f0f0',
  header_bg_color: '#ffffff',
  accent_text_color: '#2481cc',
  section_bg_color: '#ffffff',
  destructive_text_color: '#df3f40',
}

const DEV_THEME_DARK: TelegramThemeParams = {
  bg_color: '#17212b',
  text_color: '#f5f5f5',
  hint_color: '#708499',
  link_color: '#6ab3f3',
  button_color: '#5288c1',
  button_text_color: '#ffffff',
  secondary_bg_color: '#232e3c',
  header_bg_color: '#17212b',
  accent_text_color: '#6ab3f3',
  section_bg_color: '#17212b',
  destructive_text_color: '#ec3942',
}

function applyThemeParams(params: TelegramThemeParams): void {
  const root = document.documentElement
  for (const [key, cssVar] of Object.entries(CSS_VAR_BY_KEY)) {
    const value = params[key as keyof TelegramThemeParams]
    if (value) root.style.setProperty(cssVar, value)
  }
}

function applyDevTheme(): void {
  const prefersDark =
    window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
  applyThemeParams(prefersDark ? DEV_THEME_DARK : DEV_THEME_LIGHT)
  document.documentElement.dataset.theme = prefersDark ? 'dark' : 'light'
}

function applyTelegramTheme(tg: TelegramWebApp): void {
  applyThemeParams(tg.themeParams || {})
  document.documentElement.dataset.theme = tg.colorScheme || 'light'
}

// ------------------------------- Safe-area ------------------------------- //
/**
 * В полноэкранном режиме Telegram рисует свои кнопки (закрыть/свернуть) поверх
 * верха приложения. contentSafeAreaInset даёт отступ, чтобы под них не залезать;
 * safeAreaInset — вырезы устройства. Суммируем и кладём в CSS-переменные,
 * которые база берёт через max(env(), var(--tg-safe-*)).
 */
function applyInsets(tg: TelegramWebApp): void {
  const zero: TelegramInset = { top: 0, bottom: 0, left: 0, right: 0 }
  const sa = tg.safeAreaInset ?? zero
  const csa = tg.contentSafeAreaInset ?? zero
  const root = document.documentElement
  root.style.setProperty('--tg-safe-top', `${Math.max(0, sa.top + csa.top)}px`)
  root.style.setProperty('--tg-safe-bottom', `${Math.max(0, sa.bottom + csa.bottom)}px`)
  root.style.setProperty('--tg-safe-left', `${Math.max(0, sa.left + csa.left)}px`)
  root.style.setProperty('--tg-safe-right', `${Math.max(0, sa.right + csa.right)}px`)
}

// --------------------------------- init --------------------------------- //

let inited = false

export function initTelegram(): void {
  if (inited) return
  inited = true

  const tg = getWebApp()
  if (tg && tg.initData) {
    tg.ready()
    tg.expand()
    applyTelegramTheme(tg)
    applyInsets(tg)
    tg.onEvent('themeChanged', () => applyTelegramTheme(tg))
    // Пересчитываем отступы при изменении инсетов / входе в полноэкранный режим.
    tg.onEvent('safeAreaChanged', () => applyInsets(tg))
    tg.onEvent('contentSafeAreaChanged', () => applyInsets(tg))
    tg.onEvent('fullscreenChanged', () => applyInsets(tg))
    tg.onEvent('viewportChanged', () => applyInsets(tg))
  } else {
    // Dev-режим: обычный браузер. Тема из системной, live-переключение.
    applyDevTheme()
    if (window.matchMedia) {
      window
        .matchMedia('(prefers-color-scheme: dark)')
        .addEventListener('change', applyDevTheme)
    }
  }
}

// --------------------------------- Ссылки --------------------------------- //

/**
 * Открыть ссылку на Telegram (t.me/…): внутри мини-аппа — нативным методом
 * (клиент сам свернёт приложение и перейдёт в чат), вне Telegram или на старых
 * клиентах — обычной вкладкой браузера.
 */
export function openTelegramLink(url: string): void {
  if (!url) return
  const tg = getWebApp()
  const isTme = /^https:\/\/(t\.me|telegram\.me)\//i.test(url)
  if (tg?.openTelegramLink && isTme) {
    try {
      tg.openTelegramLink(url)
      return
    } catch {
      /* старый клиент — уходим в фолбэк */
    }
  }
  if (tg?.openLink && !isTme) {
    try {
      tg.openLink(url)
      return
    } catch {
      /* фолбэк ниже */
    }
  }
  window.open(url, '_blank', 'noopener')
}

// --------------------------------- Haptics --------------------------------- //

export function hapticImpact(style: HapticImpactStyle = 'light'): void {
  getWebApp()?.HapticFeedback?.impactOccurred(style)
}

export function hapticNotify(type: HapticNotificationType): void {
  getWebApp()?.HapticFeedback?.notificationOccurred(type)
}

export function hapticSelection(): void {
  getWebApp()?.HapticFeedback?.selectionChanged()
}
