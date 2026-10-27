import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { useInfiniteOutfits } from '@/lib/hooks/use-outfits'
import { api, ApiError } from '@/lib/api'
import { loggedOutfitName, useLogDayWear, useGenerateDayOptions, useUndoDayWear } from '@/lib/hooks/use-day-outfits'

vi.mock('@/lib/api', async (original) => ({
  ...(await original<typeof import('@/lib/api')>()),
  api: { get: vi.fn(), post: vi.fn() },
}))
beforeEach(() => vi.clearAllMocks())

it('sends the date and corrected items, then invalidates every wear cache including learning', async () => {
  vi.mocked(api.post).mockResolvedValue({ id: 'wear' })
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result } = renderHook(useLogDayWear, { wrapper })
  await act(() => result.current.mutateAsync({ outfitId: 'source', date: '2026-10-23', itemIds: ['a', 'b'] }))
  expect(api.post).toHaveBeenCalledWith('/outfits/source/wore', { date: '2026-10-23', item_ids: ['a', 'b'] })
  const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey)
  for (const key of ['outfits', 'calendarOutfits', 'pendingOutfits', 'analytics', 'learning', 'items', 'item', 'wear-stats', 'wear-history']) {
    expect(keys).toContainEqual([key])
  }
  expect(keys).toContainEqual(['outfit', 'source'])
  expect(keys).toContainEqual(['outfit', 'wear'])
})
it('omits optional items for an unchanged outfit', async () => {
  vi.mocked(api.post).mockResolvedValue({ id: 'source' })
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
  const { result } = renderHook(useLogDayWear, { wrapper })
  await act(() => result.current.mutateAsync({ outfitId: 'source', date: '2026-10-24' }))
  expect(api.post).toHaveBeenCalledWith('/outfits/source/wore', { date: '2026-10-24' })
})
it('resolves the conflict name against the existing outfit', async () => {
  vi.mocked(api.get).mockResolvedValue({ name: 'Blue shirt', occasion: 'casual' })
  const error = new ApiError('conflict', 409, { detail: { error_code: 'DAY_LOGGED', existing_ids: ['existing'] } })
  expect(await loggedOutfitName(error)).toBe('Blue shirt')
  expect(api.get).toHaveBeenCalledWith('/outfits/existing')
})
it.each([new Error('offline'), new ApiError('other', 409, {}), new ApiError('bad', 422, null), new ApiError('missing', 409, { detail: { error_code: 'DAY_LOGGED', existing_ids: [] } })])('does not treat malformed or unrelated errors as a logged day: %s', async (error) => {
  expect(await loggedOutfitName(error)).toBeNull()
  expect(api.get).not.toHaveBeenCalled()
})

it('generates dated options through the shared hook and refreshes day caches', async () => {
  vi.mocked(api.post).mockResolvedValue([])
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result } = renderHook(useGenerateDayOptions, { wrapper })
  await act(() => result.current.mutateAsync({ date: '2026-10-25', occasion: 'casual' }))
  expect(api.post).toHaveBeenCalledWith('/outfits/suggest-options', { date: '2026-10-25', occasion: 'casual' })
  expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toContainEqual(['outfits'])
})

it('undoes wear and refreshes item, calendar and learning caches', async () => {
  vi.mocked(api.post).mockResolvedValue({ id: 'source', learning_stale: false })
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result } = renderHook(useUndoDayWear, { wrapper })
  await act(() => result.current.mutateAsync({ outfitId: 'source', washedItemIds: [] }))
  expect(api.post).toHaveBeenCalledWith('/outfits/source/undo-wear', { washed_item_ids: [] })
  const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey)
  for (const key of ['outfits', 'calendarOutfits', 'pendingOutfits', 'analytics', 'learning', 'items', 'item', 'wear-stats', 'wear-history']) expect(keys).toContainEqual([key])
})

it('serializes owned-only template selection without a laundry filter', async () => {
  vi.mocked(api.get).mockResolvedValue({ outfits: [], total: 0, page: 1, page_size: 24, has_more: false })
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
  renderHook(() => useInfiniteOutfits({ source: 'manual', is_lookbook: true, owned_only: true }), { wrapper })
  await waitFor(() => expect(api.get).toHaveBeenCalledWith('/outfits', { params: { page: '1', page_size: '20', source: 'manual', is_lookbook: 'true', owned_only: 'true' } }))
})
