import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
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
let queryClient: QueryClient
const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>

// Find one control before checking visibility instead of scanning every button's styles.
function button(name: string) {
  const control = ['next', 'previous', 'dismissOutfit'].includes(name)
    ? screen.getByLabelText(name, { selector: 'button' })
    : screen.getByText(name).closest('button')
  expect(control).toHaveRole('button')
  expect(control).toHaveAccessibleName(name)
  expect(control).toBeVisible()
  expect(control!.closest('[aria-hidden="true"]')).toBeNull()
  return control!
}

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
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
afterEach(() => {
  cleanup()
  queryClient.clear()
})

async function click(name: string) {
  await act(async () => { fireEvent.click(button(name)) })
}

async function generate() {
  await click('getSuggestion')
  button('dismissOutfit')
}

it('uses the shared selected date for generation and logging and disables tomorrow', async () => {
  render(<SuggestPage />, { wrapper })
  fireEvent.click(button('next'))
  await generate()
  expect(api.post).toHaveBeenCalledWith('/outfits/suggest-options', expect.objectContaining({ date: '2026-10-25' }))
  const wearButton = button('wore')
  expect(wearButton).toBeDisabled()
  fireEvent.click(wearButton)
  expect(vi.mocked(api.post).mock.calls.some(([path]) => path.endsWith('/wore'))).toBe(false)
  fireEvent.click(button('previous'))
  fireEvent.click(button('previous'))
  await click('wore')
  button('getSuggestion')
  expect(api.post).toHaveBeenCalledWith('/outfits/suggestion/wore', { date: '2026-10-23' })
})

it('closes successful results before they can be dismissed, skipped or restarted', async () => {
  render(<SuggestPage />, { wrapper })
  await generate()
  await click('wore')
  button('getSuggestion')
  expect(screen.queryByRole('button', { hidden: true, name: 'dismissOutfit' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { hidden: true, name: 'startOver' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { hidden: true, name: 'options.generateNew' })).not.toBeInTheDocument()
  expect(vi.mocked(api.post).mock.calls.filter(([path]) => /\/(reject|skip)$/.test(path))).toEqual([])
})

it.each(['startOver', 'options.generateNew', 'dismissOutfit'])('never changes a wear already logged on the server via %s', async (action) => {
  render(<SuggestPage />, { wrapper })
  await generate()
  state.worn = true
  await click(action)
  expect(api.get).toHaveBeenCalledWith('/outfits/suggestion')
  if (action === 'options.generateNew') {
    expect(vi.mocked(api.post).mock.calls.filter(([path]) => path === '/outfits/suggest-options')).toHaveLength(2)
    button('dismissOutfit')
  } else {
    button('getSuggestion')
    expect(screen.queryByRole('button', { hidden: true, name: 'dismissOutfit' })).not.toBeInTheDocument()
  }
  expect(api.post).not.toHaveBeenCalledWith('/outfits/suggestion/reject')
  expect(api.post).not.toHaveBeenCalledWith('/outfits/suggestion/skip')
})

it('resets notices and the swap dialog when switching options', async () => {
  render(<SuggestPage />, { wrapper })
  await generate()
  fireEvent.click(button('swap'))
  expect(screen.getByRole('dialog')).toBeVisible()
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
  const options = screen.getAllByText('options.optionNumber', { selector: 'button span' }).map((label) => {
    const control = label.closest('button')!
    expect(control).toHaveAccessibleName('options.optionNumber')
    expect(control).toHaveRole('button')
    expect(control).toBeVisible()
    expect(control.closest('[aria-hidden="true"]')).toBeNull()
    return control
  })
  fireEvent.click(options[1])
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.queryByText('logged')).not.toBeInTheDocument()
})

it('does not enable generation or result wear before profile loading completes', async () => {
  const { rerender } = render(<SuggestPage />, { wrapper })
  await generate()
  state.profileReady = false
  rerender(<SuggestPage />)
  const wearButton = button('wore')
  expect(wearButton).toBeDisabled()
  expect(button('next')).toBeDisabled()
  fireEvent.click(wearButton)
  expect(vi.mocked(api.post).mock.calls.some(([path]) => path.endsWith('/wore'))).toBe(false)
})

it('pending history and Suggest results have Dismiss and no Accept', async () => {
  const history = render(<OutfitHistoryCard outfit={outfit} onFeedback={vi.fn()} />, { wrapper })
  expect(within(history.container).getByRole('button', { hidden: true, name: 'dismiss' })).toBeVisible()
  expect(within(history.container).queryByRole('button', { hidden: true, name: /accept/i })).not.toBeInTheDocument()
  history.unmount()
  render(<SuggestPage />, { wrapper })
  await generate()
  button('dismissOutfit')
  expect(screen.queryByRole('button', { hidden: true, name: /accept|loveIt|chooseThis/i })).not.toBeInTheDocument()
})

it('blocks the initial generation form until the profile resolves', () => {
  state.profileReady = false
  render(<SuggestPage />, { wrapper })
  const generateButton = button('getSuggestion')
  expect(generateButton).toBeDisabled()
  fireEvent.click(generateButton)
  expect(api.post).not.toHaveBeenCalled()
})

it('keeps the day view outside the narrow Suggest form', () => {
  render(<SuggestPage />, { wrapper })
  const dayView = screen.getByRole('heading', { name: 'title', level: 2 }).closest('section')!
  expect(dayView).not.toBeNull()
  expect(dayView.closest('.max-w-2xl')).toBeNull()
  const narrowForm = button('getSuggestion').closest('.max-w-2xl')!
  expect(narrowForm).not.toBeNull()
  expect(dayView.parentElement).toBe(narrowForm.parentElement)
})
