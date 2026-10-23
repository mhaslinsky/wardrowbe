import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import OutfitsPage from '@/app/dashboard/outfits/page'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/hooks/query-keys'
import { useCalendarOutfits, useInfiniteOutfits } from '@/lib/hooks/use-outfits'
import { useLogWash, useLogWear, useUpdateItem } from '@/lib/hooks/use-items'

const nav = vi.hoisted(() => ({ params: new URLSearchParams() }))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => '/dashboard/outfits',
  useSearchParams: () => nav.params,
}))
vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  setAccessToken: vi.fn(),
}))
vi.mock('@/components/outfits/outfit-card', () => ({
  OutfitCard: ({ outfit }: { outfit: { id: string } }) => <div>{outfit.id}</div>,
}))

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  client.setQueryData(['user-profile'], { timezone: 'UTC' })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

afterEach(() => {
  vi.mocked(api.get).mockReset()
  nav.params = new URLSearchParams()
})

describe('wash outfit caches', () => {
  it.each(['wash', 'wear', 'update'] as const)('invalidates outfit lists and calendar after %s', async operation => {
    vi.mocked(api.post).mockResolvedValue({})
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
    const spy = vi.spyOn(client, 'invalidateQueries')
    const { result } = renderHook(() => ({ wash: useLogWash(), wear: useLogWear(), update: useUpdateItem() }), {
      wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
    })
    if (operation === 'update') {
      await act(() => result.current.update.mutateAsync({ id: 'item', data: { needs_wash: true } }))
    } else {
      await act(() => result.current[operation].mutateAsync({ id: 'item' }))
    }
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.outfits.all })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.calendarOutfits.all })
  })
  it('keeps ready-to-wear in list requests and omits it from calendar requests', async () => {
    vi.mocked(api.get).mockResolvedValue({ outfits: [], total: 0, page: 1, has_more: false })
    renderHook(() => {
      useInfiniteOutfits({ ready_to_wear: true })
      useCalendarOutfits(2026, 10, { ready_to_wear: true })
    }, { wrapper })
    await waitFor(() => expect(vi.mocked(api.get).mock.calls.filter(([path]) => path === '/outfits')).toHaveLength(2))
    const params = vi.mocked(api.get).mock.calls.map(([, options]) => (options as { params: Record<string, string> }).params)
    expect(params.find(value => !value.date_from)?.ready_to_wear).toBe('true')
    expect(params.find(value => value.date_from)).not.toHaveProperty('ready_to_wear')
  })
})
