import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import type { Preferences } from '@/lib/types'
import SettingsPage from '@/app/dashboard/settings/page'

const state = vi.hoisted(() => ({
  preferences: { standing_rules: ['Never a polo under an overshirt'] } as Partial<Preferences> | undefined,
  save: vi.fn().mockResolvedValue({}),
  reset: vi.fn(),
}))
vi.mock('@/lib/hooks/use-preferences', () => ({
  usePreferences: () => ({ data: state.preferences, isLoading: false }),
  useUpdatePreferences: () => ({ mutateAsync: state.save, isPending: false }),
  useResetPreferences: () => ({ mutateAsync: state.reset }),
  useTestAIEndpoint: () => ({ mutateAsync: vi.fn() }),
}))
vi.mock('@/lib/hooks/use-user', () => ({
  useUserProfile: () => ({ data: undefined, isLoading: false }),
  useUpdateUserProfile: () => ({ mutateAsync: vi.fn() }),
}))
vi.mock('@/components/ui/select', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/components/ui/select')>(),
  Select: () => null,
}))
vi.mock('@/lib/api', () => ({ api: { get: vi.fn() } }))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

beforeEach(() => {
  state.preferences = { standing_rules: [] }
  state.save.mockClear()
  vi.mocked(api.get).mockReset().mockResolvedValue({ 'cool-winter': { best: ['black', 'white', 'navy', 'blue', 'purple', 'silver'], avoid: ['orange', 'brown', 'tan', 'gold', 'cream'] } })
  vi.stubGlobal('localStorage', { getItem: vi.fn(), setItem: vi.fn() })
})
afterEach(() => vi.unstubAllGlobals())

vi.mock('@/components/preview-photo-settings', () => ({ PreviewPhotoSettings: () => null }))
vi.mock('@/components/fit-profile-settings', () => ({ FitProfileSettings: () => null }))
vi.mock('@/components/access-token-settings', () => ({ AccessTokenSettings: () => null }))

it('offers all seasons, saves a choice, and clears it', async () => {
  render(<SettingsPage />)
  const select = screen.getByLabelText('colorSeason.label')
  expect(select).toHaveValue('')
  expect(select.querySelectorAll('option')).toHaveLength(13)
  expect(screen.getByText('colorSeason.help').closest('a')).toHaveAttribute('href', 'https://colorwise.me/')
  fireEvent.change(select, { target: { value: 'cool-winter' } })
  fireEvent.click(screen.getAllByText('save', { selector: 'button' })[0])
  await waitFor(() => expect(state.save).toHaveBeenCalledWith(expect.objectContaining({
    color_season: 'cool-winter',
  })))
  state.save.mockClear()
  fireEvent.change(select, { target: { value: '' } })
  fireEvent.click(screen.getAllByText('save', { selector: 'button' })[0])
  await waitFor(() => expect(state.save).toHaveBeenCalledWith(expect.objectContaining({
    color_season: null,
  })))
})

it('loads the saved season from the preferences field', () => {
  const original = state.preferences
  try {
    state.preferences = { standing_rules: [], color_season: 'cool-winter' }
    render(<SettingsPage />)
    expect(screen.getByLabelText('colorSeason.label')).toHaveValue('cool-winter')
  } finally { state.preferences = original }
})

it.each(['cool-winter', null] as const)('omits an unchanged season when saving a slider (%s)', async (season) => {
  const original = state.preferences
  try {
    state.preferences = { standing_rules: [], color_season: season }
    render(<SettingsPage />)
    fireEvent.keyDown(screen.getAllByRole('slider')[0], { key: 'ArrowRight' })
    fireEvent.click(screen.getAllByText('save', { selector: 'button' })[0])
    await waitFor(() => expect(state.save).toHaveBeenCalled())
    expect(state.save.mock.calls[0][0]).not.toHaveProperty('color_season')
    expect(state.save.mock.calls[0][0]).toHaveProperty('style_profile')
  } finally { state.preferences = original }
})

it('omits an untouched season when a refetch arrives during a style edit', async () => {
  const original = state.preferences
  try {
    state.preferences = { standing_rules: [], color_season: null }
    const { rerender } = render(<SettingsPage />)
    fireEvent.keyDown(screen.getAllByRole('slider')[0], { key: 'ArrowRight' })
    state.preferences = { standing_rules: [], color_season: 'cool-winter' }
    rerender(<SettingsPage />)
    fireEvent.click(screen.getAllByText('save', { selector: 'button' })[0])
    await waitFor(() => expect(state.save).toHaveBeenCalled())
    expect(state.save.mock.calls[0][0]).not.toHaveProperty('color_season')
  } finally { state.preferences = original }
})

it('clears the season dirty flag after a successful save', async () => {
  render(<SettingsPage />)
  const save = screen.getAllByText('save', { selector: 'button' })[0]
  fireEvent.change(screen.getByLabelText('colorSeason.label'), { target: { value: 'cool-winter' } })
  fireEvent.click(screen.getByRole('button', { name: 'colorSeason.notNow' }))
  fireEvent.click(save)
  await waitFor(() => expect(save).toBeDisabled())
  state.save.mockClear()
  fireEvent.keyDown(screen.getAllByRole('slider')[0], { key: 'ArrowRight' })
  fireEvent.click(save)
  await waitFor(() => expect(state.save).toHaveBeenCalled())
  expect(state.save.mock.calls[0][0]).not.toHaveProperty('color_season')
})

it.each([false, true])('keeps a season edit made while saving (season submitted: %s)', async (submittedSeason) => {
  let finishSave!: (value: unknown) => void
  const pending = new Promise((resolve) => { finishSave = resolve })
  state.save.mockReturnValueOnce(pending)
  render(<SettingsPage />)
  const season = screen.getByLabelText('colorSeason.label')
  const save = screen.getAllByText('save', { selector: 'button' })[0]
  if (submittedSeason) fireEvent.change(season, { target: { value: 'cool-winter' } })
  else fireEvent.keyDown(screen.getAllByRole('slider')[0], { key: 'ArrowRight' })
  fireEvent.click(save)
  expect(state.save).toHaveBeenCalledTimes(1)
  if (!submittedSeason) expect(state.save.mock.calls[0][0]).not.toHaveProperty('color_season')
  const selected = submittedSeason ? 'warm-autumn' : 'cool-winter'
  fireEvent.change(season, { target: { value: selected } })
  await act(async () => { finishSave({}); await pending })
  expect(save).toBeEnabled()
  fireEvent.click(save)
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(2))
  expect(state.save.mock.calls[1][0]).toHaveProperty('color_season', selected)
})


it.each([false, true])('previews and applies both lists only on Save (existing picks: %s)', async (existing) => {
  state.preferences = {
    standing_rules: [],
    color_favorites: existing ? ['orange'] : [],
    color_avoid: existing ? ['black'] : [],
  }
  render(<SettingsPage />)
  fireEvent.change(screen.getByLabelText('colorSeason.label'), { target: { value: 'cool-winter' } })
  const dialog = await screen.findByRole('dialog', { name: 'colorSeason.cool-winter' })
  const useColors = within(dialog).getByRole('button', { name: 'colorSeason.useColors' })
  await waitFor(() => expect(useColors).toBeEnabled())
  expect(api.get).toHaveBeenCalledWith('/users/me/preferences/color-seasons')
  expect(within(dialog).queryByText('colorSeason.replaceColors') !== null).toBe(existing)
  for (const [label, colors] of [
    ['colors.favoriteColors', ['black', 'white', 'navy', 'blue', 'purple', 'silver']],
    ['colors.colorsToAvoid', ['orange', 'brown', 'tan', 'gold', 'cream']],
  ] as const) {
    const group = within(dialog).getByRole('group', { name: label })
    expect(within(group).getAllByRole('img').map((swatch) => swatch.getAttribute('aria-label'))).toEqual(colors)
  }
  expect(state.save).not.toHaveBeenCalled()
  fireEvent.click(useColors)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(state.save).not.toHaveBeenCalled()
  fireEvent.click(screen.getAllByText('save', { selector: 'button' })[0])
  await waitFor(() => expect(state.save).toHaveBeenCalledWith(expect.objectContaining({
    color_season: 'cool-winter',
    color_favorites: ['black', 'white', 'navy', 'blue', 'purple', 'silver'],
    color_avoid: ['orange', 'brown', 'tan', 'gold', 'cream'],
  })))
  state.save.mockClear()
  fireEvent.click(screen.getAllByTitle('black')[0])
  fireEvent.click(screen.getAllByText('save', { selector: 'button' })[0])
  await waitFor(() => expect(state.save).toHaveBeenCalled())
  expect(state.save.mock.calls[0][0].color_favorites).not.toContain('black')
})

it.each(['not-now', 'escape'])('dismisses the palette with %s and keeps both lists and season', async (dismissal) => {
  state.preferences = { standing_rules: [], color_favorites: ['orange'], color_avoid: ['black'] }
  render(<SettingsPage />)
  fireEvent.change(screen.getByLabelText('colorSeason.label'), { target: { value: 'cool-winter' } })
  const dialog = await screen.findByRole('dialog')
  await waitFor(() => expect(within(dialog).getByRole('button', { name: 'colorSeason.useColors' })).toBeEnabled())
  if (dismissal === 'not-now') fireEvent.click(within(dialog).getByRole('button', { name: 'colorSeason.notNow' }))
  else fireEvent.keyDown(dialog, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  fireEvent.click(screen.getAllByText('save', { selector: 'button' })[0])
  await waitFor(() => expect(state.save).toHaveBeenCalledWith(expect.objectContaining({
    color_season: 'cool-winter', color_favorites: ['orange'], color_avoid: ['black'],
  })))
})

it('clears a season without opening the palette or changing colors', async () => {
  state.preferences = { standing_rules: [], color_favorites: ['blue'], color_avoid: ['tan'] }
  render(<SettingsPage />)
  fireEvent.change(screen.getByLabelText('colorSeason.label'), { target: { value: 'cool-winter' } })
  const dialog = await screen.findByRole('dialog')
  await waitFor(() => expect(within(dialog).getByRole('button', { name: 'colorSeason.useColors' })).toBeEnabled())
  fireEvent.click(within(dialog).getByRole('button', { name: 'colorSeason.notNow' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  vi.mocked(api.get).mockClear()
  fireEvent.change(screen.getByLabelText('colorSeason.label'), { target: { value: '' } })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(api.get).not.toHaveBeenCalled()
  fireEvent.click(screen.getAllByText('save', { selector: 'button' })[0])
  await waitFor(() => expect(state.save).toHaveBeenCalledWith(expect.objectContaining({
    color_season: null, color_favorites: ['blue'], color_avoid: ['tan'],
  })))
})

it.each([undefined, {}, { 'cool-winter': { best: [], avoid: [] } }])('reports a missing or malformed palette without clearing colors (%j)', async (response) => {
  vi.mocked(api.get).mockResolvedValueOnce(response)
  state.preferences = { standing_rules: [], color_favorites: ['blue'], color_avoid: ['tan'] }
  render(<SettingsPage />)
  fireEvent.change(screen.getByLabelText('colorSeason.label'), { target: { value: 'cool-winter' } })
  const dialog = await screen.findByRole('dialog')
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('colorSeason.loadFailed')
  expect(within(dialog).getByRole('button', { name: 'colorSeason.useColors' })).toBeDisabled()
  fireEvent.click(within(dialog).getByRole('button', { name: 'colorSeason.notNow' }))
  fireEvent.click(screen.getAllByText('save', { selector: 'button' })[0])
  await waitFor(() => expect(state.save).toHaveBeenCalledWith(expect.objectContaining({ color_favorites: ['blue'], color_avoid: ['tan'] })))
})

it('shows palette request failures without applying colors', async () => {
  vi.mocked(api.get).mockRejectedValueOnce(new Error('offline'))
  render(<SettingsPage />)
  fireEvent.change(screen.getByLabelText('colorSeason.label'), { target: { value: 'cool-winter' } })
  const dialog = await screen.findByRole('dialog')
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('colorSeason.loadFailed')
  expect(within(dialog).getByRole('button', { name: 'colorSeason.useColors' })).toBeDisabled()
})


it('keeps a dismissed request from applying colors when it completes', async () => {
  let finishRequest!: (value: unknown) => void
  const pending = new Promise((resolve) => { finishRequest = resolve })
  vi.mocked(api.get).mockReturnValueOnce(pending)
  state.preferences = { standing_rules: [], color_favorites: ['blue'], color_avoid: ['tan'] }
  render(<SettingsPage />)
  fireEvent.change(screen.getByLabelText('colorSeason.label'), { target: { value: 'cool-winter' } })
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByRole('button', { name: 'colorSeason.useColors' })).toBeDisabled()
  fireEvent.click(within(dialog).getByRole('button', { name: 'colorSeason.notNow' }))
  await act(async () => { finishRequest({ 'cool-winter': { best: ['black'], avoid: ['orange'] } }); await pending })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  fireEvent.click(screen.getAllByText('save', { selector: 'button' })[0])
  await waitFor(() => expect(state.save).toHaveBeenCalledWith(expect.objectContaining({
    color_season: 'cool-winter', color_favorites: ['blue'], color_avoid: ['tan'],
  })))
})


it('resets applied colors and the season to the saved reset response', async () => {
  state.preferences = { standing_rules: [], preview_preferences: { lines: [], tuck: 'outfit', rev: 'test-rev' } }
  state.reset.mockResolvedValueOnce({ ...state.preferences, color_season: null, color_favorites: [], color_avoid: [] })
  vi.stubGlobal('confirm', vi.fn(() => true))
  render(<SettingsPage />)
  fireEvent.change(screen.getByLabelText('colorSeason.label'), { target: { value: 'cool-winter' } })
  const dialog = await screen.findByRole('dialog')
  const useColors = within(dialog).getByRole('button', { name: 'colorSeason.useColors' })
  await waitFor(() => expect(useColors).toBeEnabled())
  fireEvent.click(useColors)
  fireEvent.click(screen.getByRole('button', { name: 'reset' }))
  await waitFor(() => expect(screen.getByLabelText('colorSeason.label')).toHaveValue(''))
  expect(screen.getAllByText('save', { selector: 'button' })[0]).toBeDisabled()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(state.save).not.toHaveBeenCalled()
})
