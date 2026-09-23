// 案件 store：集中管理当前案件，并以 SSE 优先、单定时器轮询兜底同步进度
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '@/api/endpoints'
import type { CaseDetail, ProgressEvent } from '@/types'

export const useCaseStore = defineStore('case', () => {
  const current = ref<CaseDetail | null>(null)
  const loading = ref(false)
  const error = ref('')
  const liveEvents = ref<ProgressEvent[]>([])

  let timer: number | null = null
  let es: EventSource | null = null
  let activeLiveId = ''
  let liveSession = 0
  let loadSequence = 0

  const isRunning = computed(
    () => current.value?.status === 'running' || current.value?.status === 'queued',
  )

  function clearCurrent() {
    loadSequence += 1
    current.value = null
    loading.value = false
    error.value = ''
    liveEvents.value = []
    stopLive()
  }

  async function load(id: string, quiet = false) {
    const changedCase = current.value?.id !== id
    const sequence = ++loadSequence
    if (changedCase) {
      stopLive()
      current.value = null
      liveEvents.value = []
    }
    if (!quiet || changedCase) {
      loading.value = true
      error.value = ''
    }
    try {
      const detail = await api.getCase(id)
      if (sequence !== loadSequence) return null
      current.value = detail
      return detail
    } catch (e) {
      if (sequence !== loadSequence) return null
      if (changedCase) current.value = null
      error.value = e instanceof Error ? e.message : '加载失败'
      return null
    } finally {
      if (sequence === loadSequence) loading.value = false
    }
  }

  function stopLive() {
    liveSession += 1
    activeLiveId = ''
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    if (es) {
      es.close()
      es = null
    }
  }

  function appendEvents(events: ProgressEvent[]) {
    const existing = new Set(liveEvents.value.map((event) => event.i))
    const additions = events.filter((event) => !existing.has(event.i))
    if (additions.length) liveEvents.value.push(...additions)
  }

  async function pollOnce(id = activeLiveId) {
    if (!id || id !== activeLiveId) return
    try {
      const after = liveEvents.value.length
        ? Math.max(...liveEvents.value.map((event) => event.i))
        : -1
      const res = await api.pollEvents(id, after)
      if (id !== activeLiveId) return
      appendEvents(res.events.map((event) => ({ ...event, ts: 0 })))
      if (res.status !== current.value?.status || !['running', 'queued'].includes(res.status)) {
        await load(id, true)
      }
      if (!['running', 'queued'].includes(res.status)) stopLive()
    } catch {
      // 临时网络错误由下一次单定时器轮询恢复。
    }
  }

  function scheduleTick(id: string, session: number, delay: number) {
    if (timer !== null) clearTimeout(timer)
    timer = window.setTimeout(async () => {
      timer = null
      if (session !== liveSession || id !== activeLiveId) return
      if (es) await load(id, true)
      else await pollOnce(id)
      if (session !== liveSession || id !== activeLiveId) return
      if (current.value?.id === id && isRunning.value) {
        scheduleTick(id, session, es ? 3000 : 1500)
      } else {
        stopLive()
      }
    }, delay)
  }

  /** 开启实时进度；重复调用只保留当前案件的一个 EventSource 和一个兜底定时器。 */
  function startLive(id: string) {
    stopLive()
    activeLiveId = id
    const session = liveSession
    liveEvents.value = []

    try {
      es = new EventSource(`/api/cases/${encodeURIComponent(id)}/events`)
      es.addEventListener('progress', (event) => {
        if (session !== liveSession || id !== activeLiveId) return
        try {
          appendEvents([JSON.parse((event as MessageEvent).data) as ProgressEvent])
        } catch {
          // 忽略无法解析的单条事件，不影响后续事件。
        }
      })
      es.addEventListener('done', () => {
        if (session !== liveSession || id !== activeLiveId) return
        void load(id, true).finally(() => stopLive())
      })
      es.onerror = () => {
        if (session !== liveSession || id !== activeLiveId) return
        es?.close()
        es = null
        scheduleTick(id, session, 0)
      }
    } catch {
      es = null
    }
    scheduleTick(id, session, es ? 3000 : 0)
  }

  return {
    current,
    loading,
    error,
    liveEvents,
    isRunning,
    clearCurrent,
    load,
    startLive,
    stopLive,
    pollOnce,
  }
})
