/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string
  readonly VITE_DEV_INIT_DATA: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}

// --------------------- Telegram WebApp (минимально нужное) --------------------- //
interface TelegramThemeParams {
  bg_color?: string
  text_color?: string
  hint_color?: string
  link_color?: string
  button_color?: string
  button_text_color?: string
  secondary_bg_color?: string
  header_bg_color?: string
  accent_text_color?: string
  section_bg_color?: string
  section_header_text_color?: string
  subtitle_text_color?: string
  destructive_text_color?: string
}

type HapticImpactStyle = 'light' | 'medium' | 'heavy' | 'rigid' | 'soft'
type HapticNotificationType = 'error' | 'success' | 'warning'

interface TelegramHapticFeedback {
  impactOccurred(style: HapticImpactStyle): void
  notificationOccurred(type: HapticNotificationType): void
  selectionChanged(): void
}

interface TelegramInset {
  top: number
  bottom: number
  left: number
  right: number
}

interface TelegramWebApp {
  initData: string
  initDataUnsafe: Record<string, unknown>
  version: string
  platform: string
  colorScheme: 'light' | 'dark'
  themeParams: TelegramThemeParams
  isExpanded: boolean
  viewportHeight: number
  viewportStableHeight: number
  headerColor: string
  backgroundColor: string
  // Инсеты (Bot API 8.0+): устройство + область, занятая UI Telegram (в fullscreen).
  safeAreaInset?: TelegramInset
  contentSafeAreaInset?: TelegramInset
  isFullscreen?: boolean
  HapticFeedback?: TelegramHapticFeedback
  ready(): void
  expand(): void
  close(): void
  setHeaderColor?(color: string): void
  setBackgroundColor?(color: string): void
  /** Открывает t.me-ссылку внутри Telegram (мини-апп при этом сворачивается). */
  openTelegramLink?(url: string): void
  openLink?(url: string, options?: { try_instant_view?: boolean }): void
  onEvent(event: string, cb: () => void): void
  offEvent(event: string, cb: () => void): void
}

interface TelegramNamespace {
  WebApp?: TelegramWebApp
}

interface Window {
  Telegram?: TelegramNamespace
  SpeechRecognition?: SpeechRecognitionCtor
  webkitSpeechRecognition?: SpeechRecognitionCtor
}

// --------------------- Web Speech API (минимально нужное) --------------------- //
interface SpeechRecognitionAlternative {
  transcript: string
  confidence: number
}
interface SpeechRecognitionResult {
  readonly length: number
  isFinal: boolean
  [index: number]: SpeechRecognitionAlternative
}
interface SpeechRecognitionResultList {
  readonly length: number
  [index: number]: SpeechRecognitionResult
}
interface SpeechRecognitionEventLike extends Event {
  readonly resultIndex: number
  readonly results: SpeechRecognitionResultList
}
interface SpeechRecognitionLike {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  start(): void
  stop(): void
  abort(): void
  onresult: ((event: SpeechRecognitionEventLike) => void) | null
  onerror: ((event: Event) => void) | null
  onend: (() => void) | null
  onstart: (() => void) | null
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike
