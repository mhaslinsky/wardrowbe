import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DayOutfits, WearActions, canSwap, shiftDay } from '@/components/day-outfits'
import type { Item, Outfit } from '@/lib/types'

const state = vi.hoisted(() => ({
  log: vi.fn().mockResolvedValue({}), name: vi.fn().mockResolvedValue('Blue outfit'),
  date: '', worn: false, fail: false, outfit: {} as Outfit, items: [] as Item[], post: vi.fn().mockResolvedValue([]),
}))
vi.mock('@/lib/api', () => ({ api: { post: (...args: unknown[]) => state.post(...args) } }))
vi.mock('@/lib/hooks/use-user', () => ({ useUserToday: () => () => '2026-10-24' }))
vi.mock('@/lib/hooks/use-preferences', () => ({ usePreferences: () => ({ data: { default_occasion: 'casual' } }) }))
vi.mock('@/lib/hooks/use-day-outfits', () => ({
  useLogDayWear: () => ({ mutateAsync: state.log, isPending: false }),
  loggedOutfitName: () => state.name(),
  useDayOutfits: (date: string) => { state.date = date; return { isError: state.fail, data: { pages: [{ total: 1, outfits: [{ ...state.outfit, feedback: state.worn ? { worn_at: date } : null }] }] } } },
}))
vi.mock('@/lib/hooks/use-outfits', () => ({
  useInfiniteOutfits: () => ({ data: { pages: [{ total: 1, outfits: [state.outfit] }] } }),
  useRejectOutfit: () => ({ mutate: vi.fn() }),
}))
vi.mock('@/lib/hooks/use-items', () => ({ useItemPages: () => ({ items: state.items }) }))
vi.mock('@/components/outfits/outfit-card', () => ({ OutfitCard: ({ outfit }: { outfit: Outfit }) => <div>{outfit.name}</div> }))
const item = (id: string, type = 'shirt', extra = {}) => ({ id, name: id, type, status: 'ready', needs_wash: false, is_archived: false, ...extra } as Item)
const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>

beforeEach(() => {
  vi.clearAllMocks()
  state.log.mockResolvedValue({})
  state.worn = false; state.fail = false
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

it('requires matching complete body slots and excludes duplicate items', () => {
  expect(canSwap('dress', item('top'), [])).toBe(false)
  expect(canSwap('shirt', item('same', 't-shirt'), ['same'])).toBe(false)
  expect(canSwap('dress', item('dress', 'dress'), [])).toBe(true)
  expect(shiftDay('2026-03-08', -1)).toBe('2026-03-07')
})
