import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DayOutfits, WearActions } from '@/components/day-outfits'
import { translationMock } from './setup'
import type { Item, Outfit } from '@/lib/types'

const state = vi.hoisted(() => ({
  log: vi.fn().mockResolvedValue({}), name: vi.fn().mockResolvedValue('Blue outfit'),
  extraOutfits: [] as Outfit[], loading: false, more: false, profileReady: true, date: '', worn: false, fail: false, outfit: {} as Outfit, items: [] as Item[], post: vi.fn().mockResolvedValue([]),
}))
vi.mock('@/lib/api', () => ({ api: { post: (...args: unknown[]) => state.post(...args) } }))
vi.mock('@/lib/hooks/use-user', () => ({ useUserToday: () => () => '2026-10-24', useUserProfile: () => ({ data: state.profileReady ? { timezone: 'America/New_York' } : undefined }) }))
vi.mock('@/lib/hooks/use-preferences', () => ({ usePreferences: () => ({ data: { default_occasion: 'casual' } }) }))
vi.mock('@/lib/hooks/use-day-outfits', async (original) => ({
  ...(await original<typeof import('@/lib/hooks/use-day-outfits')>()),
  useLogDayWear: () => ({ mutateAsync: state.log, isPending: false }),
  loggedOutfitName: () => state.name(),
  useDayOutfits: (date: string) => { state.date = date; return { isError: state.fail, data: { pages: [{ total: 1, outfits: [{ ...state.outfit, feedback: state.worn ? { worn_at: date } : null }, ...state.extraOutfits] }] } } },
}))
vi.mock('@/lib/hooks/use-outfits', () => ({
  useInfiniteOutfits: () => ({ data: { pages: [{ total: 1, outfits: [state.outfit] }] } }),
  useRejectOutfit: () => ({ mutate: vi.fn() }),
}))
vi.mock('@/lib/hooks/use-items', () => ({ useItemPages: () => ({ items: state.items, isLoading: state.loading, hasMore: state.more }) }))
vi.mock('@/components/outfits/outfit-card', () => ({ OutfitCard: ({ outfit, hideWashWarnings }: { outfit: Outfit; hideWashWarnings?: boolean }) => <div>{outfit.name}{!hideWashWarnings && <span>Wash</span>}</div> }))
const item = (id: string, type = 'shirt', extra = {}) => ({ id, name: id, type, status: 'ready', needs_wash: false, is_archived: false, ...extra } as Item)
const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>

beforeEach(() => {
  vi.clearAllMocks()
  state.extraOutfits = []
  state.log.mockResolvedValue({})
  translationMock.includeNamespace = false; state.loading = false; state.more = false; state.profileReady = true; state.worn = false; state.fail = false
  state.outfit = { id: 'o1', name: 'Look', status: 'pending', items: [item('shirt'), item('pants', 'pants')] } as unknown as Outfit
  state.items = [item('clean', 't-shirt'), item('dirty', 'shirt', { needs_wash: true }), item('archived', 'shirt', { is_archived: true }), item('processing', 'shirt', { status: 'processing' }), item('boots', 'boots')]
})

describe('day view', () => {
  it('opens today, deduplicates outfits, navigates to past and tomorrow, and resets', () => {
    render(<DayOutfits />, { wrapper })
    expect(state.date).toBe('2026-10-24')
    expect(screen.getAllByText('Look')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'previous' }))
    expect(state.date).toBe('2026-10-23')
    expect(screen.queryByRole('button', { name: 'generate' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'today' }))
    fireEvent.click(screen.getByRole('button', { name: 'next' }))
    expect(state.date).toBe('2026-10-25')
    expect(screen.getByRole('button', { name: 'next' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'wore' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'swap' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'today' }))
    expect(state.date).toBe('2026-10-24')
  })
  it('requests options for the selected date', async () => {
    render(<DayOutfits />, { wrapper })
    fireEvent.click(screen.getByRole('button', { name: 'next' }))
    fireEvent.click(screen.getByRole('button', { name: 'generate' }))
    await waitFor(() => expect(state.post).toHaveBeenCalledWith('/outfits/suggest-options', { date: '2026-10-25', occasion: 'casual' }))
  })
  it('does not generate or offer wear for the logged day', () => {
    state.worn = true
    render(<DayOutfits />, { wrapper })
    expect(screen.getByRole('button', { name: 'generate' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'wore' })).not.toBeInTheDocument()
  })
  it('shows query failures and blocks generation', () => {
    state.fail = true
    render(<DayOutfits />, { wrapper })
    expect(screen.getByRole('alert')).toHaveTextContent('failed')
    expect(screen.getByRole('button', { name: 'generate' })).toBeDisabled()
  })
})

describe('wear actions', () => {
  it('logs an existing outfit for a past day', async () => {
    render(<WearActions outfit={state.outfit} date="2026-10-23" />)
    fireEvent.click(screen.getByRole('button', { name: 'wore' }))
    await waitFor(() => expect(state.log).toHaveBeenCalledWith({ outfitId: 'o1', date: '2026-10-23', itemIds: undefined }))
    expect(await screen.findByRole('status')).toHaveTextContent('logged')
  })
  it('swaps a clean same-slot piece and sends the full item list', async () => {
    render(<WearActions outfit={state.outfit} date="2026-10-24" />)
    fireEvent.click(screen.getByRole('button', { name: 'swap' }))
    expect(screen.queryByRole('option', { name: 'dirty' })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'archived' })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'processing' })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'boots' })).not.toBeInTheDocument()
    fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'clean' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'wore' }).at(-1)!)
    await waitFor(() => expect(state.log).toHaveBeenCalledWith({ outfitId: 'o1', date: '2026-10-24', itemIds: ['clean', 'pants'] }))
  })
  it('shows the logged-day conflict', async () => {
    state.log.mockRejectedValueOnce(new Error('conflict'))
    render(<WearActions outfit={state.outfit} date="2026-10-24" />)
    fireEvent.click(screen.getByRole('button', { name: 'wore' }))
    expect(await screen.findByRole('status')).toHaveTextContent('alreadyLogged')
  })
  it('shows a failed request and permits a retry', async () => {
    state.log.mockRejectedValueOnce(new Error('offline')); state.name.mockResolvedValueOnce(null)
    render(<WearActions outfit={state.outfit} date="2026-10-24" />)
    fireEvent.click(screen.getByRole('button', { name: 'wore' }))
    expect(await screen.findByRole('status')).toHaveTextContent('failed')
    fireEvent.click(screen.getByRole('button', { name: 'wore' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('logged'))
  })
  it('disables empty outfits', () => {
    render(<WearActions outfit={{ ...state.outfit, items: [] }} date="2026-10-24" />)
    expect(screen.getByRole('button', { name: 'wore' })).toBeDisabled()
  })
})

it('requires complete body slots and excludes duplicate pieces in the picker', () => {
  state.outfit = { ...state.outfit, items: [item('dress', 'dress'), item('other-dress', 'dress')] } as unknown as Outfit
  state.items = [item('top'), item('other-dress', 'dress'), item('new-dress', 'dress')]
  render(<WearActions outfit={state.outfit} date="2026-10-24" />)
  fireEvent.click(screen.getByRole('button', { name: 'swap' }))
  const select = screen.getAllByRole('combobox')[0]
  expect(Array.from(select.querySelectorAll('option')).map((option) => option.value)).toEqual(['dress', 'new-dress'])
})

it('logs the previous day through the day view and posts nothing for tomorrow', async () => {
  render(<DayOutfits />, { wrapper })
  fireEvent.click(screen.getByRole('button', { name: 'previous' }))
  fireEvent.click(screen.getByRole('button', { name: 'wore' }))
  await waitFor(() => expect(state.log).toHaveBeenCalledWith({ outfitId: 'o1', date: '2026-10-23', itemIds: undefined }))
  state.log.mockClear()
  fireEvent.click(screen.getByRole('button', { name: 'today' }))
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  expect(screen.getByRole('button', { name: 'wore' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'wore' }))
  expect(state.log).not.toHaveBeenCalled()
})

it('clears logged notice when the date or outfit changes', async () => {
  const { rerender } = render(<WearActions outfit={state.outfit} date="2026-10-24" />)
  fireEvent.click(screen.getByRole('button', { name: 'wore' }))
  expect(await screen.findByRole('status')).toHaveTextContent('logged')
  rerender(<WearActions outfit={state.outfit} date="2026-10-23" />)
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'swap' }))
  rerender(<WearActions outfit={{ ...state.outfit, id: 'o2' }} date="2026-10-23" />)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it.each(['2026-10-23', 'outfit'])('ignores a wear that finishes after navigating to %s', async (destination) => {
  let finish!: (outfit: unknown) => void
  state.log.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
  const success = vi.fn()
  const { rerender } = render(<WearActions outfit={state.outfit} date="2026-10-24" onSuccess={success} />)
  fireEvent.click(screen.getByRole('button', { name: 'wore' }))
  rerender(<WearActions outfit={{ ...state.outfit, id: destination === 'outfit' ? 'o2' : 'o1' }} date={destination === 'outfit' ? '2026-10-24' : destination} onSuccess={success} />)
  await act(async () => finish({ id: 'logged' }))
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(success).not.toHaveBeenCalled()
})

it('disables date controls, generation and wear until the profile loads', () => {
  state.profileReady = false
  const { rerender } = render(<DayOutfits />, { wrapper })
  for (const name of ['previous', 'next', 'today', 'generate', 'wore', 'swap']) {
    expect(screen.getByRole('button', { name })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name }))
  }
  expect(state.log).not.toHaveBeenCalled()
  expect(state.post).not.toHaveBeenCalled()
  state.profileReady = true
  rerender(<DayOutfits />)
  expect(screen.getByRole('button', { name: 'previous' })).toBeEnabled()
  expect(screen.getByRole('button', { name: 'wore' })).toBeEnabled()
})

it('uses a separate generation failure message', async () => {
  state.post.mockRejectedValueOnce(new Error('offline'))
  render(<DayOutfits />, { wrapper })
  fireEvent.click(screen.getByRole('button', { name: 'generate' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('generateFailed')
})

it('reuses common loading and load-more strings in the swap picker', () => {
  translationMock.includeNamespace = true
  state.loading = true; state.more = true
  render(<WearActions outfit={state.outfit} date="2026-10-24" />)
  fireEvent.click(screen.getByRole('button', { name: 'outfits.day.swap' }))
  expect(screen.getByText('common.loading')).toBeVisible()
  expect(screen.getByRole('button', { name: 'common.loadMore' })).toBeVisible()
  translationMock.includeNamespace = false
})

it('labels today, tomorrow and another day without a preview notice', () => {
  render(<DayOutfits />, { wrapper })
  expect(screen.getByText('today')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  expect(screen.getByText('tomorrow')).toBeVisible()
  expect(screen.queryByText('preview')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'today' }))
  fireEvent.click(screen.getByRole('button', { name: 'previous' }))
  expect(screen.getByText('Fri, Oct 23')).toBeVisible()
})

it('separates the worn outfit and hides its wash warnings', () => {
  state.worn = true
  render(<DayOutfits />, { wrapper })
  const worn = screen.getByRole('heading', { name: 'worn' }).parentElement!
  expect(within(worn).getByText('Look')).toBeVisible()
  expect(within(worn).queryByText('Wash')).not.toBeInTheDocument()
  const suggestions = screen.getByRole('heading', { name: 'options' }).parentElement!
  expect(within(suggestions).queryByText('Look')).not.toBeInTheDocument()
})

it('keeps dismiss beside wear and swap', () => {
  render(<DayOutfits />, { wrapper })
  const row = screen.getByRole('button', { name: 'wore' }).parentElement
  expect(row).toBe(screen.getByRole('button', { name: 'dismiss' }).parentElement)
  expect(row).toBe(screen.getByRole('button', { name: 'swap' }).parentElement)
  expect(row).not.toHaveClass('flex-wrap')
})

it('shows every logged outfit once outside the suggestion sections', () => {
  state.worn = true
  state.extraOutfits = [{ ...state.outfit, id: 'second-log', name: 'Second log', feedback: { worn_at: '2026-10-24' } } as Outfit]
  const { rerender } = render(<DayOutfits />, { wrapper })
  const worn = screen.getByRole('heading', { name: 'worn' }).parentElement!
  expect(within(worn).getByText('Look')).toBeVisible()
  expect(within(worn).getByText('Second log')).toBeVisible()
  expect(within(worn).queryByText('Wash')).not.toBeInTheDocument()
  expect(screen.getAllByText('Second log')).toHaveLength(1)
  expect(screen.getAllByText('Look')).toHaveLength(1)
  rerender(<DayOutfits />)
  expect(within(worn).getByText('Second log')).toBeVisible()
  state.worn = false
  state.extraOutfits = []
  rerender(<DayOutfits />)
  expect(screen.queryByRole('heading', { name: 'worn' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'wore' })).toBeEnabled()
})

it('uses an icon with an accessible Today label and no button text', () => {
  render(<DayOutfits />, { wrapper })
  const today = screen.getByRole('button', { name: 'today' })
  expect(today.querySelector('svg')).toBeInTheDocument()
  expect(today).toHaveTextContent('')
  expect(today.textContent).toBe('')
})
