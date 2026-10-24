import { QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { UndoWearActions } from '@/components/undo-wear-actions'
import { api, ApiError, NetworkError } from '@/lib/api'
import type { Outfit } from '@/lib/hooks/use-outfits'
import { createQueryClient } from '@/lib/query-client'
import { toast } from 'sonner'
import { translationMock } from './setup'
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }))
vi.mock('@/lib/api', async (original) => ({ ...(await original<typeof import('@/lib/api')>()), api: { post: vi.fn(), get: vi.fn() } }))
const outfit = { id: 'logged', items: [{ id: 'shirt', status: 'ready', is_archived: false }], name: 'Logged' } as Outfit
const chosen = { ...outfit, id: 'saved', name: 'Saved' }
const washed = new ApiError('wash', 409, { detail: { error_code: 'WASH_SINCE_WEAR', items: [
  { id: 'shirt', name: 'Shirt', washed_at: '2026-10-24' }, { id: 'pants', name: 'Pants', washed_at: '2026-10-24' },
] } })
beforeEach(() => { vi.clearAllMocks(); translationMock.includeNamespace = false; vi.mocked(api.post).mockResolvedValue({ id: 'logged', learning_stale: false }); vi.mocked(api.get).mockResolvedValue({ items: [{ id: 'shirt', status: 'ready', is_archived: false }, { id: 'archived', status: 'ready', is_archived: true }, { id: 'processing', status: 'processing', is_archived: false }], page: 1, has_more: false }) })
function setup(choices = [chosen]) {
  const client = createQueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const notice = vi.fn()
  function Harness() {
    const [message, setMessage] = useState('')
    return <QueryClientProvider client={client}><UndoWearActions outfit={outfit} date="2026-10-23" choices={choices} onNotice={(text) => { notice(text); setMessage(text) }} /><p role="status">{message}</p></QueryClientProvider>
  }
  render(<Harness />)
  return { notice, invalidate }
}
it('undoes without wash and invalidates every wear cache', async () => {
  const { notice, invalidate } = setup()
  fireEvent.click(screen.getByRole('button', { name: 'undo' }))
  await waitFor(() => expect(notice).toHaveBeenLastCalledWith('undone'))
  expect(api.post).toHaveBeenCalledWith('/outfits/logged/undo-wear', {})
  const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey)
  for (const key of ['outfits', 'calendarOutfits', 'pendingOutfits', 'analytics', 'learning', 'items', 'item', 'wear-stats', 'wear-history', 'wash-history']) expect(keys).toContainEqual([key])
  expect(keys).toContainEqual(['outfit', 'logged'])
})
it.each([false, true])('checks every washed item and resends selected ids, empty=%s', async (empty) => {
  vi.mocked(api.post).mockRejectedValueOnce(washed); setup()
  fireEvent.click(screen.getByRole('button', { name: 'undo' }))
  expect(await screen.findByRole('dialog')).toHaveAccessibleName('washedSince')
  expect(toast.error).not.toHaveBeenCalled()
  expect(screen.getByRole('checkbox', { name: 'Shirt' })).toBeChecked()
  expect(screen.getByRole('checkbox', { name: 'Pants' })).toBeChecked()
  fireEvent.click(screen.getByRole('checkbox', { name: 'Pants' }))
  if (empty) fireEvent.click(screen.getByRole('checkbox', { name: 'Shirt' }))
  fireEvent.click(screen.getByRole('button', { name: 'confirm' }))
  await waitFor(() => expect(api.post).toHaveBeenLastCalledWith('/outfits/logged/undo-wear', { washed_item_ids: empty ? [] : ['shirt'] }))
})
it('replaces after undo and wash confirmation using the selected date', async () => {
  vi.mocked(api.post).mockRejectedValueOnce(washed)
  const { notice } = setup()
  fireEvent.click(screen.getByRole('button', { name: 'replace' })); fireEvent.click(await screen.findByRole('button', { name: 'Saved' }))
  await screen.findByRole('checkbox', { name: 'Shirt' })
  expect(api.post).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: 'confirm' }))
  await waitFor(() => expect(notice).toHaveBeenLastCalledWith('logged'))
  expect(vi.mocked(api.post).mock.calls.map(([path]) => path)).toEqual(['/outfits/logged/undo-wear', '/outfits/logged/undo-wear', '/outfits/saved/wore'])
  expect(api.post).toHaveBeenLastCalledWith('/outfits/saved/wore', { date: '2026-10-23' })
})
it('leaves the day open with an error when replacement fails', async () => {
  vi.mocked(api.post).mockResolvedValueOnce({ learning_stale: false }).mockRejectedValueOnce(new Error('offline'))
  const { notice, invalidate } = setup()
  fireEvent.click(screen.getByRole('button', { name: 'replace' })); fireEvent.click(await screen.findByRole('button', { name: 'Saved' }))
  await waitFor(() => expect(notice).toHaveBeenLastCalledWith('replaceFailed'))
  expect(api.post).toHaveBeenCalledTimes(2)
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['calendarOutfits'] })
})
it('reports stale learning', async () => {
  vi.mocked(api.post).mockResolvedValueOnce({ learning_stale: true })
  const { notice } = setup(); fireEvent.click(screen.getByRole('button', { name: 'undo' }))
  await waitFor(() => expect(notice).toHaveBeenLastCalledWith('learningStale'))
})
it.each([new Error('offline'), new ApiError('missing', 409, null), new ApiError('bad', 409, { detail: { error_code: 'WASH_SINCE_WEAR', items: [{}] } }), new ApiError('not worn', 409, { detail: { error_code: 'NOT_WORN' } }), new ApiError('inconsistent', 409, { detail: { error_code: 'UNDO_INCONSISTENT' } }), new ApiError('invalid', 422, {})])('shows failures without logging a replacement: %s', async (error) => {
  vi.mocked(api.post).mockRejectedValueOnce(error)
  const { notice } = setup()
  fireEvent.click(screen.getByRole('button', { name: 'replace' })); fireEvent.click(await screen.findByRole('button', { name: 'Saved' }))
  await waitFor(() => expect(notice).toHaveBeenLastCalledWith('failed'))
  expect(api.post).toHaveBeenCalledTimes(1)
})
it('handles empty choices and cancels wash confirmation without undoing', async () => {
  vi.mocked(api.post).mockRejectedValueOnce(washed); setup([])
  fireEvent.click(screen.getByRole('button', { name: 'replace' })); expect(await screen.findByText('empty')).toBeVisible()
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
  fireEvent.click(screen.getByRole('button', { name: 'undo' })); await screen.findByRole('checkbox', { name: 'Shirt' })
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
  expect(api.post).toHaveBeenCalledTimes(1); expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('allows confirmation for an unnamed washed item', async () => {
  vi.mocked(api.post).mockRejectedValueOnce(new ApiError('wash', 409, { detail: { error_code: 'WASH_SINCE_WEAR', items: [{ id: 'shirt', name: null, washed_at: '2026-10-24' }] } }))
  setup(); fireEvent.click(screen.getByRole('button', { name: 'undo' }))
  expect(await screen.findByRole('checkbox', { name: 'unnamedItem' })).toBeChecked()
})

it.each([false, true])('closes dialogs and toasts a real undo failure after wash=%s', async (washFirst) => {
  if (washFirst) vi.mocked(api.post).mockRejectedValueOnce(washed)
  vi.mocked(api.post).mockRejectedValueOnce(new ApiError('server failed', 500, {}))
  setup()
  fireEvent.click(screen.getByRole('button', { name: 'replace' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Saved' }))
  if (washFirst) { await screen.findByRole('checkbox', { name: 'Shirt' }); fireEvent.click(screen.getByRole('button', { name: 'confirm' })) }
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('failed'))
  expect(screen.getByRole('status')).toBeVisible()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(toast.error).toHaveBeenCalledWith('server failed')
})
it.each([false, true])('toasts replacement failures and uses one notice when stale=%s', async (stale) => {
  vi.mocked(api.post).mockResolvedValueOnce({ learning_stale: stale }).mockRejectedValueOnce(new ApiError('wear failed', 422, {}))
  setup(); fireEvent.click(screen.getByRole('button', { name: 'replace' })); fireEvent.click(await screen.findByRole('button', { name: 'Saved' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(stale ? 'replaceFailedStale' : 'replaceFailed'))
  expect(screen.getByRole('status')).toBeVisible()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(toast.error).toHaveBeenCalledWith('wear failed')
})
it('excludes unavailable choices and translates unnamed outfit occasions', async () => {
  translationMock.includeNamespace = true
  setup([
    { ...chosen, id: 'archived', name: 'Archived', items: [{ ...chosen.items[0], id: 'archived' }] },
    { ...chosen, id: 'processing', name: 'Processing', items: [{ ...chosen.items[0], id: 'processing' }] },
    { ...chosen, id: 'empty', name: 'Empty', items: [] },
    { ...chosen, name: null, occasion: 'casual' },
  ] as Outfit[])
  fireEvent.click(screen.getByRole('button', { name: 'outfits.day.replace' }))
  await screen.findByRole('button', { name: 'constants.occasions.casual' })
  for (const name of ['Archived', 'Processing', 'Empty', 'casual']) expect(screen.queryByRole('button', { name, exact: true })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'constants.occasions.casual' })).toBeVisible()
  expect(api.post).not.toHaveBeenCalled()
})

it.each([new ApiError('unavailable', 503, {}), new NetworkError('offline')])('preserves normal toasts for undo errors: %s', async (error) => {
  vi.mocked(api.post).mockRejectedValueOnce(error)
  setup(); fireEvent.click(screen.getByRole('button', { name: 'undo' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('failed'))
  expect(screen.getByRole('status')).toBeVisible()
  expect(vi.mocked(toast.error).mock.calls[0][0]).toBe(error.message)
})

it('automatically loads all ready pages before offering any replacement', async () => {
  let finish!: (value: unknown) => void
  vi.mocked(api.get).mockResolvedValueOnce({ items: [{ id: 'shirt', status: 'ready' }], page: 1, has_more: true })
    .mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
  setup([chosen, { ...chosen, id: 'second', name: 'Page two outfit', items: [{ ...chosen.items[0], id: 'pants' }] }])
  fireEvent.click(screen.getByRole('button', { name: 'replace' }))
  await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2))
  expect(screen.getByText('loading')).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Saved' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'loadMore' })).not.toBeInTheDocument()
  finish({ items: [{ id: 'pants', status: 'ready' }], page: 2, has_more: false })
  expect(await screen.findByRole('button', { name: 'Page two outfit' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Saved' })).toBeVisible()
  expect(screen.queryByText('loading')).not.toBeInTheDocument()
})
it('shows an error and no choices when a later ready page fails', async () => {
  vi.mocked(api.get).mockResolvedValueOnce({ items: [{ id: 'shirt', status: 'ready' }], page: 1, has_more: true })
    .mockRejectedValue(new ApiError('page failed', 500, {}))
  setup(); fireEvent.click(screen.getByRole('button', { name: 'replace' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('failed')
  expect(screen.queryByRole('button', { name: 'Saved' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'loadMore' })).not.toBeInTheDocument()
  expect(api.post).not.toHaveBeenCalled()
})
