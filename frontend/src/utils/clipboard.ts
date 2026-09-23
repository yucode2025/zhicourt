export type ShareResult = 'share' | 'clipboard' | 'fallback'

function legacyCopy(text: string): boolean {
  const input = document.createElement('textarea')
  input.value = text
  input.readOnly = true
  input.setAttribute('aria-label', '可复制链接')
  input.style.position = 'fixed'
  input.style.opacity = '0'
  document.body.appendChild(input)
  input.select()
  input.setSelectionRange(0, input.value.length)
  let copied = false
  try {
    copied = typeof document.execCommand === 'function' && document.execCommand('copy')
  } finally {
    input.remove()
  }
  return copied
}

export async function shareOrCopy(url: string, title: string): Promise<ShareResult> {
  if (navigator.share) {
    try {
      await navigator.share({ title, url })
      return 'share'
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') throw error
    }
  }

  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(url)
      return 'clipboard'
    } catch {
      // 在非安全上下文或权限拒绝时继续使用同步复制回退。
    }
  }

  if (legacyCopy(url)) return 'clipboard'
  return 'fallback'
}
