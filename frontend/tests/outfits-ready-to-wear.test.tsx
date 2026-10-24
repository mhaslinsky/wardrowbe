import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import OutfitsPage from '@/app/dashboard/outfits/page'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/hooks/query-keys'
import { useBulkDeleteOutfits } from '@/lib/hooks/use-outfits'

const nav = vi.hoisted(() => ({ params: new URLSearchParams() }))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => '/dashboard/outfits',
  useSearchParams: () => nav.params,
}))
vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn() },
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

describe('ready to wear', () => {
  it('requests clean outfits and restores the full list when reset', async () => {
    vi.mocked(api.get).mockImplementation(async (_path, options) => {
      const params = (options as { params: Record<string, string> }).params
      if (params.is_lookbook) return { outfits: [], total: 1, page: 1, page_size: 1, has_more: false }
      return { outfits: params.ready_to_wear ? [{ id: 'clean' }] : [{ id: 'clean' }, { id: 'dirty' }], total: params.ready_to_wear ? 1 : 2, page: 1, page_size: 24, has_more: false }
    })
    render(<OutfitsPage />, { wrapper })
    expect(await screen.findByText('dirty')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'filters.readyToWear' }))
    await waitFor(() => expect(screen.queryByText('dirty')).not.toBeInTheDocument())
    expect(await screen.findByText('clean')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'filters.readyToWear' }))
    expect(await screen.findByText('dirty')).toBeInTheDocument()
  })
})


it('selects all matching ready outfits and sends the active filter for deletion', async () => {
  vi.mocked(api.get).mockImplementation(async (_path, options) => {
    const params = (options as { params: Record<string, string> }).params
    if (params.is_lookbook) return { outfits: [], total: 0, page: 1, page_size: 1, has_more: false }
    return { outfits: [{ id: 'clean' }], total: 2, page: 1, page_size: 24, has_more: true }
  })
  vi.mocked(api.post).mockResolvedValue({ deleted: 2, failed: 0, errors: [] })
  render(<OutfitsPage />, { wrapper })
  await screen.findByText('clean')
  fireEvent.click(screen.getByRole('button', { name: 'filters.readyToWear' }))
  await waitFor(() => expect(api.get).toHaveBeenCalledWith('/outfits', expect.objectContaining({
    params: expect.objectContaining({ ready_to_wear: 'true' }),
  })))
  fireEvent.click(screen.getByRole('button', { name: 'bulkActions.select' }))
  fireEvent.click(screen.getByText('selectAll'))
  fireEvent.click(screen.getByRole('button', { name: 'bulkActions.selectAllMatching' }))
  fireEvent.click(screen.getByRole('button', { name: 'delete' }))
  fireEvent.click(screen.getAllByRole('button', { name: 'delete' }).at(-1)!)
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/outfits/bulk/delete', {
    select_all: true,
    excluded_ids: [],
    filters: expect.objectContaining({ ready_to_wear: true }),
  }))
})
