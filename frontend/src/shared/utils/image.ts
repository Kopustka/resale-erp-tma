/**
 * Уменьшение фото перед отправкой.
 *
 * Телефон отдаёт снимок как есть — в хранилище лежат файлы по 2–4 МБ, а на
 * вещь их три-четыре. То есть до 9 МБ выгрузки с мобильной сети на каждое
 * добавление: именно это ощущается как «долго добавляется», а не работа
 * сервера (он отвечает за десятки миллисекунд).
 *
 * 1600px хватает с запасом: водяной знак и так ужимает кадр до этого
 * размера, а миниатюры списка берут 400px.
 */

const MAX_SIDE = 1600
const QUALITY = 0.82
/** Мельче этого сжимать нечего — только потеряем качество на пересжатии. */
const SKIP_BELOW_BYTES = 400 * 1024

export interface Downscaled {
  file: File
  originalBytes: number
  bytes: number
}

function canDownscale(): boolean {
  return (
    typeof createImageBitmap === 'function' &&
    typeof document !== 'undefined' &&
    !!document.createElement('canvas').getContext
  )
}

/**
 * Возвращает уменьшенный файл либо исходный, если сжимать нечего или
 * браузер не умеет. Никогда не бросает: неудачное сжатие не должно
 * мешать добавить вещь.
 */
export async function downscaleForUpload(file: File): Promise<Downscaled> {
  const originalBytes = file.size
  const asIs: Downscaled = { file, originalBytes, bytes: originalBytes }

  if (!file.type.startsWith('image/')) return asIs
  if (originalBytes <= SKIP_BELOW_BYTES) return asIs
  if (!canDownscale()) return asIs

  let bitmap: ImageBitmap | null = null
  try {
    // imageOrientation: телефон пишет кадр «лёжа» с EXIF-тегом поворота.
    // Canvas тег не переносит, поэтому разворачиваем пиксели при декоде —
    // иначе вертикальные фото ушли бы на сервер повёрнутыми.
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })

    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height))
    const w = Math.round(bitmap.width * scale)
    const h = Math.round(bitmap.height * scale)

    const canvas = document.createElement('canvas')
    canvas.width = w
    canvas.height = h
    const ctx = canvas.getContext('2d')
    if (!ctx) return asIs
    ctx.drawImage(bitmap, 0, 0, w, h)

    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, 'image/jpeg', QUALITY),
    )
    if (!blob || blob.size >= originalBytes) return asIs // не помогло — шлём как есть

    const name = file.name.replace(/\.[^.]+$/, '') || 'photo'
    return {
      file: new File([blob], `${name}.jpg`, { type: 'image/jpeg' }),
      originalBytes,
      bytes: blob.size,
    }
  } catch {
    return asIs
  } finally {
    bitmap?.close?.()
  }
}
