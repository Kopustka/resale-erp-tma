/**
 * Мини-санитайзер Telegram-HTML без внешних зависимостей.
 *
 * Сервер возвращает готовую подпись поста в разметке Telegram. Перед выводом
 * через v-html оставляем только белый список тегов; всё остальное «разворачиваем»
 * (тег выкидываем, текст сохраняем), опасные контейнеры вырезаем целиком.
 * Атрибуты не переносим вовсе — кроме href у ссылок с безопасной схемой.
 */

/** Теги, поддерживаемые Telegram-разметкой. */
const ALLOWED_TAGS = new Set([
  'B',
  'STRONG',
  'I',
  'EM',
  'U',
  'S',
  'CODE',
  'PRE',
  'A',
  'BR',
  'BLOCKQUOTE',
  'TG-SPOILER',
])

/** Контейнеры, которые вырезаем вместе с содержимым. */
const DROP_TAGS = new Set([
  'SCRIPT',
  'STYLE',
  'IFRAME',
  'OBJECT',
  'EMBED',
  'LINK',
  'META',
  'NOSCRIPT',
  'TEMPLATE',
  'SVG',
  'MATH',
  'FORM',
  'INPUT',
  'BUTTON',
])

const SAFE_HREF = /^(https?:|tg:|mailto:)/i

function sanitizeNode(node: Node): Node[] {
  if (node.nodeType === Node.TEXT_NODE) {
    return [document.createTextNode(node.nodeValue ?? '')]
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return []

  const el = node as Element
  const tag = el.tagName.toUpperCase()
  if (DROP_TAGS.has(tag)) return []

  const children: Node[] = []
  for (const child of Array.from(el.childNodes)) children.push(...sanitizeNode(child))

  // Не из белого списка — разворачиваем: сам тег теряем, детей оставляем.
  if (!ALLOWED_TAGS.has(tag)) return children

  const clean = document.createElement(tag.toLowerCase())
  if (tag === 'A') {
    const href = (el.getAttribute('href') ?? '').trim()
    if (SAFE_HREF.test(href)) clean.setAttribute('href', href)
  }
  for (const c of children) clean.appendChild(c)
  return [clean]
}

/** Возвращает безопасный HTML-фрагмент для v-html. */
export function sanitizeTelegramHtml(html: string): string {
  if (!html) return ''
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  const holder = document.createElement('div')
  for (const child of Array.from(parsed.body.childNodes)) {
    for (const clean of sanitizeNode(child)) holder.appendChild(clean)
  }
  return holder.innerHTML
}
