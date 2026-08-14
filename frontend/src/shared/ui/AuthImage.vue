<script setup lang="ts">
/**
 * Изображение из /api/v1/media/{item}/{index}, которое требует заголовок initData.
 * Тег <img> заголовок не пошлёт, поэтому грузим fetch -> blob -> objectURL.
 * Lazy через IntersectionObserver + плейсхолдер при отсутствии фото/ошибке.
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchBlob } from '@/shared/api/http'
import { mediaPath } from '@/shared/api/endpoints'

const props = withDefaults(
  defineProps<{
    itemId: string
    index?: number
    photoCount?: number
    alt?: string
    /**
     * Ширина миниатюры. Без неё сервер отдаёт оригинал с телефона —
     * в списке это мегабайты на строку высотой в сотню пикселей.
     */
    width?: number
  }>(),
  { index: 0, photoCount: 1, alt: '' },
)

const url = ref<string | null>(null)
const loading = ref(false)
const failed = ref(false)
const root = ref<HTMLElement | null>(null)
let objectUrl: string | null = null
let observer: IntersectionObserver | null = null
let controller: AbortController | null = null

function hasPhoto(): boolean {
  return props.photoCount > props.index
}

function revoke(): void {
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl)
    objectUrl = null
  }
}

async function load(): Promise<void> {
  if (!hasPhoto() || url.value || loading.value) return
  loading.value = true
  failed.value = false
  controller = new AbortController()
  try {
    const blob = await fetchBlob(
      mediaPath(props.itemId, props.index, props.width),
      controller.signal,
    )
    revoke()
    objectUrl = URL.createObjectURL(blob)
    url.value = objectUrl
  } catch (e) {
    if (!(e instanceof DOMException && e.name === 'AbortError')) failed.value = true
  } finally {
    loading.value = false
  }
}

function reset(): void {
  controller?.abort()
  revoke()
  url.value = null
  failed.value = false
}

onMounted(() => {
  if (!root.value) return
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          void load()
          observer?.disconnect()
          observer = null
          break
        }
      }
    },
    { rootMargin: '200px' },
  )
  observer.observe(root.value)
})

watch(
  () => [props.itemId, props.index] as const,
  () => {
    reset()
    void load()
  },
)

onBeforeUnmount(() => {
  observer?.disconnect()
  controller?.abort()
  revoke()
})
</script>

<template>
  <div ref="root" class="auth-image">
    <img v-if="url" :src="url" :alt="alt" class="img" />
    <div v-else class="ph" :class="{ 'ph-loading': loading }">
      <svg v-if="!loading" viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
        <path
          fill="currentColor"
          d="M4 5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2H4zm0 2h16v7.6l-3.3-3.3a1 1 0 0 0-1.4 0L11 15.6l-1.8-1.8a1 1 0 0 0-1.4 0L4 17.6V7zm4.5 1A1.5 1.5 0 1 0 10 9.5 1.5 1.5 0 0 0 8.5 8z"
        />
      </svg>
    </div>
  </div>
</template>

<style scoped>
.auth-image {
  width: 100%;
  height: 100%;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: var(--tg-theme-secondary-bg-color);
}
.img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.ph {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--tg-theme-hint-color);
  background: var(--tg-theme-secondary-bg-color);
}
.ph-loading {
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  0%,
  100% {
    opacity: 0.5;
  }
  50% {
    opacity: 0.9;
  }
}
</style>
