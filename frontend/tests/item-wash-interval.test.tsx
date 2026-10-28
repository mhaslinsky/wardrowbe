import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ItemDetailDialog } from '@/components/item-detail-dialog'
import type { Item } from '@/lib/types'
import { api } from '@/lib/api'

vi.unmock('next-intl')
vi.mock('@/lib/api', () => ({
  api: { get: vi.fn().mockResolvedValue([]), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  setAccessToken: vi.fn(),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

function messages(locale: string, namespace: string) {
  return JSON.parse(
    readFileSync(resolve(__dirname, '..', 'messages', locale, `${namespace}.json`), 'utf8'),
  )
}

function wrapperFor(locale: string) {
  const all = Object.fromEntries(
    ['wardrobe', 'constants', 'common'].map((ns) => [ns, messages(locale, ns)]),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <NextIntlClientProvider locale={locale} messages={all} onError={() => {}}>
          {children}
        </NextIntlClientProvider>
      </QueryClientProvider>
    )
  }
}

const baseItem = {
  id: 'wash-item', type: 'shirt', status: 'ready', tags: {}, images: [],
  favorite: false, wear_count: 0, wears_since_wash: 0, effective_wash_interval: 2,
} as unknown as Item

async function openEditor(item: Item) {
  render(<ItemDetailDialog item={item} open onOpenChange={() => {}} />, {
    wrapper: wrapperFor('en'),
  })
  fireEvent.click(screen.getByRole('button', { name: 'Edit item' }))
  return screen.getByRole('spinbutton')
}

async function saveInterval(expected: number | null) {
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(api.patch).toHaveBeenCalled())
  const body = JSON.parse(JSON.stringify(vi.mocked(api.patch).mock.calls.at(-1)?.[1]))
  expect(body.wash_interval).toBe(expected)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Edit item' })).toBeInTheDocument())
  vi.mocked(api.patch).mockClear()
}

beforeEach(() => {
  vi.mocked(api.patch).mockReset().mockResolvedValue(baseItem)
})

describe('wash interval editing', () => {
  it('clears an override and restores the default placeholder after reload', async () => {
    const input = await openEditor({ ...baseItem, wash_interval: 8, effective_wash_interval: 8 })
    fireEvent.change(input, { target: { value: '' } })
    await saveInterval(null)
    cleanup()
    const reloaded = await openEditor(baseItem)
    expect(reloaded).toHaveValue(null)
    expect(reloaded).toHaveAttribute('placeholder', 'Default: 2')
    await saveInterval(null)
  })

  it('sets, clears, and sets an interval again', async () => {
    let input = await openEditor(baseItem)
    fireEvent.change(input, { target: { value: '7' } })
    await saveInterval(7)
    fireEvent.click(screen.getByRole('button', { name: 'Edit item' }))
    input = screen.getByRole('spinbutton')
    fireEvent.change(input, { target: { value: '' } })
    await saveInterval(null)
    fireEvent.click(screen.getByRole('button', { name: 'Edit item' }))
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '5' } })
    await saveInterval(5)
  })

  it('saves an untouched default as no override', async () => {
    await openEditor(baseItem)
    await saveInterval(null)
  })

  it.each(['1.5', '0', '101'])('rejects malformed interval %s before sending an update', async (value) => {
    const input = await openEditor(baseItem)
    fireEvent.change(input, { target: { value } })
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()
    fireEvent.click(save)
    expect(api.patch).not.toHaveBeenCalled()
  })

  it.each(['-', '1e'])('blocks browser-reported incomplete input %s until corrected', async () => {
    const input = await openEditor({ ...baseItem, wash_interval: 8 })
    let badInput = true
    Object.defineProperty(input, 'validity', {
      configurable: true,
      get: () => ({ badInput }),
    })
    fireEvent.change(input, { target: { value: '' } })
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()
    fireEvent.click(save)
    expect(api.patch).not.toHaveBeenCalled()
    badInput = false
    fireEvent.change(input, { target: { value: '4' } })
    expect(save).toBeEnabled()
    await saveInterval(4)
  })

  it('allows other edits with an unchanged unlimited interval', async () => {
    await openEditor({ ...baseItem, wash_interval: 999, effective_wash_interval: 999 })
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
    await saveInterval(999)
  })

})
