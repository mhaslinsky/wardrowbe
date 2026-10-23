import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import OutfitsPage from '@/app/dashboard/outfits/page'
import { OutfitCard } from '@/components/outfits/outfit-card'
import { api } from '@/lib/api'
import type { Outfit } from '@/lib/hooks/use-outfits'

const nav = vi.hoisted(() => ({ params: new URLSearchParams(), replace: vi.fn() }))
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: nav.replace }),
  useSearchParams: () => nav.params,
}))
vi.mock('@/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() }, setAccessToken: vi.fn() }))

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  client.setQueryData(['user-profile'], { timezone: 'UTC' })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const template = {
  id: 'template', name: 'Saved look', occasion: 'casual', source: 'manual',
  status: 'pending', items: [], scheduled_for: null, created_at: '2026-10-20T00:00:00Z',
  group_size: 3, wear_count: 1, hidden_member_ids: ['original', 'wear'],
} as unknown as Outfit

afterEach(() => {
  vi.mocked(api.get).mockReset()
  vi.mocked(api.post).mockReset()
  nav.replace.mockReset()
  nav.params = new URLSearchParams()
})

describe('grouped outfits', () => {
  it('requests grouping only in All, preserves clean-filter grouping, and resets it', async () => {
    vi.mocked(api.get).mockResolvedValue({ outfits: [template], total: 1, page: 1, has_more: false })
    const view = render(<OutfitsPage />, { wrapper })
    expect(await screen.findByText('Saved look')).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith('/outfits', { params: expect.objectContaining({ group: 'true' }) })
    expect(screen.queryByRole('button', { name: 'bulkActions.select' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'filters.readyToWear' }))
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/outfits', {
      params: expect.objectContaining({ group: 'true', ready_to_wear: 'true' }),
    }))
    nav.params = new URLSearchParams('filter=worn')
    view.rerender(<OutfitsPage />)
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/outfits', {
      params: expect.objectContaining({ status: 'accepted', is_lookbook: 'false' }),
    }))
    const wornCall = vi.mocked(api.get).mock.calls.find(([, options]) => options?.params?.status === 'accepted')
    expect(wornCall?.[1]?.params).not.toHaveProperty('group')
    nav.params = new URLSearchParams()
    view.rerender(<OutfitsPage />)
    expect(screen.queryByRole('button', { name: 'bulkActions.select' })).not.toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('clears bulk selection when switching from individual records to All', async () => {
    nav.params = new URLSearchParams('filter=ai')
    vi.mocked(api.get).mockResolvedValue({ outfits: [template], total: 1, page: 1, has_more: false })
    const view = render(<OutfitsPage />, { wrapper })
    fireEvent.click(await screen.findByRole('button', { name: 'bulkActions.select' }))
    expect(screen.getByRole('checkbox')).toBeInTheDocument()
    nav.params = new URLSearchParams()
    view.rerender(<OutfitsPage />)
    expect(await screen.findByText('Saved look')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('checkbox')).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'bulkActions.select' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'bulkActions.delete' })).not.toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('keeps the default All view for an empty inventory', async () => {
    vi.mocked(api.get).mockResolvedValue({ outfits: [], total: 0, page: 1, has_more: false })
    render(<OutfitsPage />, { wrapper })
    expect(await screen.findByText('empty.all')).toBeInTheDocument()
    expect(nav.replace).not.toHaveBeenCalled()
    expect(api.get).toHaveBeenCalledWith('/outfits', { params: expect.objectContaining({ group: 'true' }) })
  })

  it('keeps calendar queries ungrouped', async () => {
    nav.params = new URLSearchParams('view=calendar')
    vi.mocked(api.get).mockResolvedValue({ outfits: [], total: 0, page: 1, has_more: false })
    render(<OutfitsPage />, { wrapper })
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/outfits', {
      params: expect.objectContaining({ date_from: expect.any(String), date_to: expect.any(String) }),
    }))
    const calendarCall = vi.mocked(api.get).mock.calls.find(([, options]) => options?.params?.date_from)
    expect(calendarCall?.[1]?.params).not.toHaveProperty('group')
    expect(api.get).toHaveBeenCalledWith('/outfits', { params: expect.objectContaining({ group: 'true' }) })
  })

  it.each(['unknown', 'constructor'])('normalizes filter=%s to All and disables bulk actions', async (filter) => {
    nav.params = new URLSearchParams({ filter })
    vi.mocked(api.get).mockResolvedValue({ outfits: [template], total: 1, page: 1, has_more: false })
    render(<OutfitsPage />, { wrapper })
    expect(await screen.findByText('Saved look')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'filters.all' })).toHaveClass('border-primary')
    expect(screen.queryByRole('button', { name: 'bulkActions.select' })).not.toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith('/outfits', { params: expect.objectContaining({ group: 'true' }) })
    expect(api.post).not.toHaveBeenCalled()
  })

  it('shows wear count and links to every hidden record without nested anchors', () => {
    const { container } = render(<OutfitCard outfit={template} />, { wrapper })
    expect(screen.getByText('wearCount')).toBeInTheDocument()
    fireEvent.click(screen.getByText('history'))
    expect(screen.getAllByRole('link').map((link) => link.getAttribute('href')).sort()).toEqual([
      '/dashboard/outfits/original', '/dashboard/outfits/template', '/dashboard/outfits/wear',
    ])
    expect(container.querySelector('a a')).toBeNull()
  })
})
