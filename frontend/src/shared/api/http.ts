/**
 * HTTP-клиент. Автоматически проставляет X-TG-Init-Data на КАЖДЫЙ запрос.
 * Idempotency-Key передаётся точечно (PATCH статуса).
 */
import { getInitData } from '@/shared/telegram/webapp'

const BASE_URL = import.meta.env.VITE_API_URL ?? ''

export class ApiError extends Error {
  readonly status: number
  readonly code: string | null
  readonly details: unknown

  constructor(status: number, message: string, code: string | null = null, details: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }

  get isConflict(): boolean {
    return this.status === 409
  }
  get isUnprocessable(): boolean {
    return this.status === 422
  }
  get isForbidden(): boolean {
    return this.status === 403
  }
}

interface RequestOptions {
  method?: string
  query?: Record<string, string | number | boolean | null | undefined>
  body?: unknown
  headers?: Record<string, string>
  signal?: AbortSignal
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = new URL(`${BASE_URL}${path}`, window.location.origin)
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === null || value === undefined || value === '') continue
      url.searchParams.set(key, String(value))
    }
  }
  // Возвращаем относительный путь, если BASE_URL пустой (same-origin).
  return BASE_URL ? url.toString() : `${url.pathname}${url.search}`
}

function baseHeaders(extra?: Record<string, string>): Headers {
  const headers = new Headers(extra)
  headers.set('X-TG-Init-Data', getInitData())
  return headers
}

async function parseError(res: Response): Promise<ApiError> {
  let message = res.statusText || `HTTP ${res.status}`
  let code: string | null = null
  let details: unknown = null
  try {
    const data = await res.json()
    if (data && typeof data === 'object') {
      // FastAPI: {detail: ...} либо кастом {code,message,details}
      if ('message' in data && typeof data.message === 'string') message = data.message
      if ('code' in data && typeof data.code === 'string') code = data.code
      if ('details' in data) details = data.details
      if ('detail' in data) {
        const d = (data as { detail: unknown }).detail
        message = typeof d === 'string' ? d : JSON.stringify(d)
        details = d
      }
    }
  } catch {
    /* тело не JSON — оставляем statusText */
  }
  return new ApiError(res.status, message, code, details)
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = 'GET', query, body, headers, signal } = opts
  const h = baseHeaders(headers)
  let payload: BodyInit | undefined
  if (body !== undefined) {
    h.set('Content-Type', 'application/json')
    payload = JSON.stringify(body)
  }

  const res = await fetch(buildUrl(path, query), {
    method,
    headers: h,
    body: payload,
    signal,
  })

  if (!res.ok) throw await parseError(res)

  if (res.status === 204) return undefined as T
  const contentType = res.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) return undefined as T
  return (await res.json()) as T
}

/** Загрузка файла (multipart). Content-Type НЕ ставим — браузер сам добавит boundary. */
export async function uploadFile<T>(path: string, file: File, signal?: AbortSignal): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(buildUrl(path), {
    method: 'POST',
    headers: baseHeaders(), // только X-TG-Init-Data
    body: form,
    signal,
  })
  if (!res.ok) throw await parseError(res)
  return (await res.json()) as T
}

/** Загрузка бинарного медиа с обязательным заголовком initData (для AuthImage). */
export async function fetchBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const res = await fetch(buildUrl(path), {
    method: 'GET',
    headers: baseHeaders(),
    signal,
  })
  if (!res.ok) throw await parseError(res)
  return res.blob()
}

export const http = {
  get: <T>(path: string, query?: RequestOptions['query'], signal?: AbortSignal) =>
    request<T>(path, { method: 'GET', query, signal }),
  post: <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
    request<T>(path, { method: 'POST', body, headers }),
  patch: <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
    request<T>(path, { method: 'PATCH', body, headers }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
