import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import SuggestPage from '@/app/dashboard/suggest/page'
import { OutfitHistoryCard } from '@/components/outfit-history-card'
import { api } from '@/lib/api'
import type { Outfit } from '@/lib/types'

const state = vi.hoisted(() => ({ profileReady: true, worn: false }))
vi.mock('@/lib/api', async (original) => ({
  ...await original<typeof import('@/lib/api')>(),
  api: { get: vi.fn(), post: vi.fn() },
}))
vi.mock('@/lib/hooks/use-user', () => ({
  useUserToday: () => () => '2026-10-24',
  useUserProfile: () => ({ data: state.profileReady ? { timezone: 'America/New_York' } : undefined }),
}))
vi.mock('@/lib/hooks/use-weather', () => ({ useWeather: () => ({ data: undefined }) }))
vi.mock('@/lib/hooks/use-preferences', () => ({ usePreferences: () => ({ data: { default_occasion: 'casual' } }) }))
vi.mock('@/components/shared/item-picker', () => ({ ItemPicker: () => null }))
vi.mock('@/components/shared/occasion-chips', () => ({ OccasionChips: () => null }))
vi.mock('next/image', () => ({ default: () => null }))
const outfit = { id: 'suggestion', name: 'Look', occasion: 'casual', status: 'pending', source: 'on_demand', scheduled_for: '2026-10-24', items: [{ id: 'shirt', type: 'shirt', name: 'Shirt' }], feedback: null } as unknown as Outfit
const second = { ...outfit, id: 'second', name: 'Other look' }
const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>{children}</QueryClientProvider>

beforeEach(() => {
  vi.clearAllMocks(); state.profileReady = true; state.worn = false
  vi.mocked(api.get).mockImplementation(async (path) => {
    if (path === '/outfits/suggestion') return { ...outfit, feedback: state.worn ? { worn_at: '2026-10-24' } : null }
    return { outfits: [], items: [], total: 0, has_more: false }
  })
  vi.mocked(api.post).mockImplementation(async (path) => {
    if (path === '/outfits/suggest-options') return [outfit, second]
    if (path.endsWith('/wore')) { state.worn = true; return { ...outfit, feedback: { worn_at: '2026-10-24' } } }
    return {}
  })
})
async function generate() {
  fireEvent.click(screen.getByRole('button', { name: 'getSuggestion' }))
  await screen.findByRole('button', { name: 'dismissOutfit' })
}

it('uses the shared selected date for generation and logging and disables tomorrow', async () => {
  render(<SuggestPage />, { wrapper })
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  await generate()
  expect(api.post).toHaveBeenCalledWith('/outfits/suggest-options', expect.objectContaining({ date: '2026-10-25' }))
  expect(screen.getByRole('button', { name: 'wore' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'wore' }))
  expect(vi.mocked(api.post).mock.calls.some(([path]) => path.endsWith('/wore'))).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'previous' }))
  fireEvent.click(screen.getByRole('button', { name: 'previous' }))
  fireEvent.click(screen.getByRole('button', { name: 'wore' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/outfits/suggestion/wore', { date: '2026-10-23' }))
})

it('closes successful results before they can be dismissed, skipped or restarted', async () => {
  render(<SuggestPage />, { wrapper })
  await generate()
  fireEvent.click(screen.getByRole('button', { name: 'wore' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'dismissOutfit' })).not.toBeInTheDocument())
  expect(screen.queryByRole('button', { name: 'startOver' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'options.generateNew' })).not.toBeInTheDocument()
  expect(vi.mocked(api.post).mock.calls.filter(([path]) => /\/(reject|skip)$/.test(path))).toEqual([])
})

it.each(['startOver', 'options.generateNew', 'dismissOutfit'])('never changes a wear already logged on the server via %s', async (action) => {
  render(<SuggestPage />, { wrapper })
  await generate()
  state.worn = true
  fireEvent.click(screen.getByRole('button', { name: action }))
  await waitFor(() => expect(api.get).toHaveBeenCalledWith('/outfits/suggestion'))
  if (action === 'options.generateNew') {
    await waitFor(() => expect(vi.mocked(api.post).mock.calls.filter(([path]) => path === '/outfits/suggest-options')).toHaveLength(2))
  } else {
    await waitFor(() => expect(screen.queryByRole('button', { name: 'dismissOutfit' })).not.toBeInTheDocument())
  }
  expect(api.post).not.toHaveBeenCalledWith('/outfits/suggestion/reject')
  expect(api.post).not.toHaveBeenCalledWith('/outfits/suggestion/skip')
})

it('resets notices and the swap dialog when switching options', async () => {
  render(<SuggestPage />, { wrapper })
  await generate()
  fireEvent.click(screen.getByRole('button', { name: 'swap' }))
  expect(screen.getByRole('dialog')).toBeVisible()
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
  const options = screen.getAllByRole('button', { name: 'options.optionNumber' })
  fireEvent.click(options[1])
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.queryByText('logged')).not.toBeInTheDocument()
})

it('does not enable generation or result wear before profile loading completes', async () => {
  const { rerender } = render(<SuggestPage />, { wrapper })
  await generate()
  state.profileReady = false
  rerender(<SuggestPage />)
  expect(screen.getByRole('button', { name: 'wore' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'next' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'wore' }))
  expect(vi.mocked(api.post).mock.calls.some(([path]) => path.endsWith('/wore'))).toBe(false)
})

it('pending history and Suggest results have Dismiss and no Accept', async () => {
  const history = render(<OutfitHistoryCard outfit={outfit} onFeedback={vi.fn()} />, { wrapper })
  expect(within(history.container).getByRole('button', { name: 'dismiss' })).toBeVisible()
  expect(within(history.container).queryByRole('button', { name: /accept/i })).not.toBeInTheDocument()
  history.unmount()
  render(<SuggestPage />, { wrapper })
  await generate()
  expect(screen.getByRole('button', { name: 'dismissOutfit' })).toBeVisible()
  expect(screen.queryByRole('button', { name: /accept|loveIt|chooseThis/i })).not.toBeInTheDocument()
})

it('blocks the initial generation form until the profile resolves', () => {
  state.profileReady = false
  render(<SuggestPage />, { wrapper })
  expect(screen.getByRole('button', { name: 'getSuggestion' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'getSuggestion' }))
  expect(api.post).not.toHaveBeenCalled()
})
