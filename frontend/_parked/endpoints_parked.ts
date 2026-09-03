// Отложенные вызовы API: канал, публикации, шаблоны, скидки, нейросеть.
// Возврат — перенести обратно в shared/api/endpoints.ts.

// ------------------------------ Каналы ------------------------------ //
export const channelsApi = {
  list: () => http.get<Channel[]>(`${V1}/channels`),
  create: (payload: ChannelCreate) => http.post<Channel>(`${V1}/channels`, payload),
  update: (id: string, patch: ChannelPatch) =>
    http.patch<Channel>(`${V1}/channels/${id}`, patch),
  remove: (id: string) => http.del<void>(`${V1}/channels/${id}`),
  test: (id: string) => http.post<{ ok: boolean }>(`${V1}/channels/${id}/test`),
}

// ---------------------------- Шаблоны постов ---------------------------- //
export const templatesApi = {
  list: () => http.get<PostTemplate[]>(`${V1}/templates`),

  create: (name: string, body: string) =>
    http.post<PostTemplate>(`${V1}/templates`, { name, body }),

  update: (id: string, patch: { name?: string; body?: string }) =>
    http.patch<PostTemplate>(`${V1}/templates/${id}`, patch),

  remove: (id: string) => http.del<void>(`${V1}/templates/${id}`),

  /** Сделать шаблон активным (снимает флаг с остальных). */
  makeDefault: (id: string) => http.post<PostTemplate>(`${V1}/templates/${id}/default`),

  placeholders: () => http.get<TemplatePlaceholder[]>(`${V1}/templates/placeholders`),

  /** Рендер тела шаблона на демо-данных → HTML-подпись поста. */
  preview: (body: string) => http.post<{ caption: string }>(`${V1}/templates/preview`, { body }),

  /** Собрать шаблон по словесному описанию (не сохраняет). */
  generate: (brief: string) =>
    http.post<{ name: string; body: string }>(`${V1}/templates/generate`, { brief }),

  /** Старт сессии «скопировать дизайн из поста». */
  startCapture: () => http.post<CaptureSession>(`${V1}/templates/capture`),

  captureStatus: (token: string) => http.get<CaptureStatus>(`${V1}/templates/capture/${token}`),

  /** Снять сессию на сервере, чтобы бот перестал ждать пример поста. */
  cancelCapture: (token: string) => http.del<void>(`${V1}/templates/capture/${token}`),
}

// ------------------------------- Скидки ------------------------------- //
export const discountsApi = {
  list: (itemId?: string, includeDone = false) =>
    http.get<Discount[]>(`${V1}/discounts`, {
      item_id: itemId ?? undefined,
      include_done: includeDone || undefined,
    }),
  create: (itemId: string, newPrice: number, scheduledAt: string | null) =>
    http.post<Discount>(`${V1}/discounts`, {
      item_id: itemId,
      new_price: newPrice,
      scheduled_at: scheduledAt,
    }),
  cancel: (id: string) => http.del<void>(`${V1}/discounts/${id}`),
}

// ------------------------------- Дропы ------------------------------- //
export const dropsApi = {
  /** Опубликовать выбранные вещи одним альбомом. */
  create: (itemIds: string[], title?: string, note?: string) =>
    http.post<DropOut>(`${V1}/drops`, { item_ids: itemIds, title, note }),
}

// -------------------------- Контент-календарь -------------------------- //
export const postsApi = {
  list: (includeDone = false) =>
    http.get<CustomPost[]>(`${V1}/posts`, { include_done: includeDone || undefined }),
  create: (body: string, scheduledAt: string | null, photoFileIds: string[] = []) =>
    http.post<CustomPost>(`${V1}/posts`, {
      body,
      scheduled_at: scheduledAt,
      photo_file_ids: photoFileIds,
    }),
  cancel: (id: string) => http.del<void>(`${V1}/posts/${id}`),
}


// --- из itemsApi ---

  /** Разбор голосовой фразы в поля новой вещи. */
  parseVoice: (text: string) =>
    http.post<VoiceParseResult>(`${V1}/items/parse-voice`, { text }),

  /** Запись из мини-аппа -> распознавание и разбор нейросетью. */
  voiceUpload: (blob: Blob, signal?: AbortSignal) =>
    uploadFile<VoiceParseResult>(
      `${V1}/items/voice-upload`,
      new File([blob], 'voice.webm', { type: blob.type || 'audio/webm' }),
      signal,
    ),

  /** Начать диктовку боту: ссылка в чат, куда записать голосовое. */
  startVoiceCapture: () => http.post<VoiceCapture>(`${V1}/items/voice-capture`),

  voiceCaptureStatus: (token: string) =>
    http.get<VoiceCaptureStatus>(`${V1}/items/voice-capture/${token}`),

  cancelVoiceCapture: (token: string) =>
    http.del<void>(`${V1}/items/voice-capture/${token}`),

  /** AI-перегенерация названия/описания по фото вещи (без сохранения). */
  aiDescribe: (id: string) => http.post<AiDescribeResult>(`${V1}/items/${id}/ai-describe`),
}
