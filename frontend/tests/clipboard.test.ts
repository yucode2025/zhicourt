import { describe, expect, it, vi } from 'vitest'
import { shareOrCopy } from '@/utils/clipboard'

describe('shareOrCopy', () => {
  it('prefers native share', async () => {
    const share = vi.fn().mockResolvedValue(undefined)
    const writeText = vi.fn()
    vi.stubGlobal('navigator', { share, clipboard: { writeText } })
    await expect(shareOrCopy('https://example.com/share/1', '判决')).resolves.toBe('share')
    expect(writeText).not.toHaveBeenCalled()
  })

  it('falls through failed share to clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { share: vi.fn().mockRejectedValue(new Error('unsupported')), clipboard: { writeText } })
    await expect(shareOrCopy('https://example.com/share/1', '判决')).resolves.toBe('clipboard')
    expect(writeText).toHaveBeenCalledWith('https://example.com/share/1')
  })

  it('provides a manual-copy fallback when all automatic methods fail', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
    Object.defineProperty(document, 'execCommand', { configurable: true, value: vi.fn().mockReturnValue(false) })
    await expect(shareOrCopy('https://example.com/share/1', '判决')).resolves.toBe('fallback')
  })
})
