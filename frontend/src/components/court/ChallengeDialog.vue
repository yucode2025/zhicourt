<script setup lang="ts">
// 用户质询对话框：role=dialog + 焦点圈定 + Escape 关闭 + 焦点恢复
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, useMessage } from 'naive-ui'
import { api } from '@/api/endpoints'
import { challengeTypeLabels } from '@/utils/display'

const props = defineProps<{
  show: boolean
  caseId: string
  target: string
  targetRefId?: string
}>()

const emit = defineEmits<{ (e: 'update:show', v: boolean): void; (e: 'submitted'): void }>()

const message = useMessage()
const text = ref('')
const submitting = ref(false)
const result = ref<{ challenge_type: string; response: string } | null>(null)
const submitError = ref('')
const dlgCard = ref<HTMLElement | null>(null)
const closeBtn = ref<HTMLButtonElement | null>(null)
let lastFocused: HTMLElement | null = null

const targetNames: Record<string, string> = {
  prosecution: '控方',
  defense: '辩方',
  judge: 'Judge',
  evidence: '该条证据',
  source: '该来源',
}

function onKeydown(event: KeyboardEvent) {
  if (!props.show) return
  if (event.key === 'Escape') {
    event.stopPropagation()
    close()
    return
  }
  if (event.key === 'Tab' && dlgCard.value) {
    const focusables = dlgCard.value.querySelectorAll<HTMLElement>(
      'button, textarea, input, [href], [tabindex]:not([tabindex="-1"])',
    )
    if (!focusables.length) return
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }
}

watch(
  () => props.show,
  async (v) => {
    if (v) {
      text.value = ''
      result.value = null
      submitError.value = ''
      lastFocused = document.activeElement as HTMLElement | null
      document.addEventListener('keydown', onKeydown, true)
      await nextTick()
      closeBtn.value?.focus()
    } else {
      document.removeEventListener('keydown', onKeydown, true)
      lastFocused?.focus?.()
    }
  },
)

onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown, true))

async function submit() {
  if (submitting.value) return
  if (text.value.trim().length < 4) {
    message.warning('请输入至少 4 个字的质询内容')
    return
  }
  submitting.value = true
  submitError.value = ''
  try {
    result.value = await api.challenge(props.caseId, props.target, text.value.trim(), props.targetRefId)
    emit('submitted')
  } catch (e) {
    submitError.value = e instanceof Error ? e.message : '质询提交失败'
    message.error(submitError.value)
  } finally {
    submitting.value = false
  }
}

function close() {
  emit('update:show', false)
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="dlg-mask" @click.self="close">
      <div
        ref="dlgCard"
        class="dlg card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="challenge-dlg-title"
      >
        <div class="dlg-head">
          <h3 id="challenge-dlg-title">我来质询 · {{ targetNames[target] ?? target }}</h3>
          <button ref="closeBtn" class="close-btn" aria-label="关闭质询对话框" @click="close">×</button>
        </div>

        <template v-if="!result">
          <p class="dlg-hint">
            你可以质疑论证的逻辑、证据的可靠性、概念的定义或适用范围。系统会基于案件现有证据回应，不会编造新证据。
          </p>
          <label class="field">
            <span>质询内容（至少 4 个字）</span>
            <textarea
              v-model="text"
              class="text-input"
              rows="4"
              maxlength="1000"
              placeholder="例如：这条证据的样本是否足够大？这个推论是否因果倒置？"
            />
          </label>
          <p v-if="submitError" class="submit-error" role="alert">{{ submitError }}</p>
          <div class="dlg-foot">
            <span class="count">{{ text.length }}/1000</span>
            <div>
              <NButton size="small" @click="close">取消</NButton>
              <NButton size="small" type="primary" style="margin-left: 8px" :loading="submitting" :disabled="submitting" @click="submit">
                提交质询
              </NButton>
            </div>
          </div>
        </template>

        <template v-else>
          <div class="result-type">质询类型：{{ challengeTypeLabels[result.challenge_type] ?? result.challenge_type }}</div>
          <div class="result-body">{{ result.response }}</div>
          <div class="dlg-foot" style="justify-content: flex-end">
            <NButton size="small" type="primary" @click="close">知道了</NButton>
          </div>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.dlg-mask {
  position: fixed;
  inset: 0;
  background: rgba(26, 26, 31, 0.45);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.dlg {
  width: 560px;
  max-width: 100%;
  max-height: 82vh;
  overflow-y: auto;
  padding: 22px 24px;
}
.dlg-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.close-btn {
  background: none;
  border: none;
  font-size: 22px;
  cursor: pointer;
  color: var(--c-ink-3);
  line-height: 1;
}
.dlg-hint {
  font-size: 13px;
  color: var(--c-ink-3);
  margin: 0 0 14px;
}
.submit-error { color: var(--c-danger); font-size: 13px; margin: 10px 0 0; }
.dlg-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 14px;
}
.count {
  font-size: 12px;
  color: var(--c-ink-3);
}
.result-type {
  font-size: 13px;
  font-weight: 600;
  color: var(--c-primary);
  margin-bottom: 10px;
}
.result-body {
  font-size: 14px;
  line-height: 1.8;
  white-space: pre-wrap;
  color: var(--c-ink-2);
}
</style>
