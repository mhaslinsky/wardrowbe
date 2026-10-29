import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import type { Preferences } from '@/lib/types'
import SettingsPage from '@/app/dashboard/settings/page'

const state = vi.hoisted(() => ({
  preferences: { standing_rules: ['Never a polo under an overshirt'] } as Partial<Preferences> | undefined,
  save: vi.fn().mockImplementation(async (patch) => patch),
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
  await waitFor(() => expect(state.save).toHaveBeenCalledWith({
    color_season: 'cool-winter',
  }))
  state.save.mockClear()
  fireEvent.change(select, { target: { value: '' } })
  await waitFor(() => expect(state.save).toHaveBeenCalledWith({
    color_season: null,
  }))
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
    await waitFor(() => expect(state.save).toHaveBeenCalled())
    expect(state.save.mock.calls[0][0]).not.toHaveProperty('color_season')
  } finally { state.preferences = original }
})


it.each([false, true])('previews and saves both lists immediately on Apply (existing picks: %s)', async (existing) => {
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
  fireEvent.click(useColors)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  await waitFor(() => expect(state.save).toHaveBeenCalledWith({
    color_favorites: ['black', 'white', 'navy', 'blue', 'purple', 'silver'],
    color_avoid: ['orange', 'brown', 'tan', 'gold', 'cream'],
  }))
  state.save.mockClear()
  fireEvent.click(screen.getAllByTitle('black')[0])
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
  await waitFor(() => expect(state.save).toHaveBeenCalledWith({
    color_season: 'cool-winter',
  }))
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
  await waitFor(() => expect(state.save).toHaveBeenCalledWith({
    color_season: null,
  }))
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
  await waitFor(() => expect(state.save).toHaveBeenCalledWith({ color_season: 'cool-winter' }))
  expect(state.save.mock.calls.every(([patch]) => !('color_favorites' in patch) && !('color_avoid' in patch))).toBe(true)
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
  await waitFor(() => expect(state.save).toHaveBeenCalledWith({
    color_season: 'cool-winter',
  }))
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
  expect(screen.queryByRole('button', { name: 'save' })).not.toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('sends both palette lists even when the favorite list already matches', async () => {
  const best = ['black', 'white', 'navy', 'blue', 'purple', 'silver'];
  const avoid = ['orange', 'brown', 'tan', 'gold', 'cream'];
  state.preferences = { standing_rules: [], color_favorites: best, color_avoid: ['tan'] };
  render(<SettingsPage />);
  fireEvent.change(screen.getByLabelText('colorSeason.label'), { target: { value: 'cool-winter' } });
  const dialog = await screen.findByRole('dialog');
  const apply = within(dialog).getByRole('button', { name: 'colorSeason.useColors' });
  await waitFor(() => expect(apply).toBeEnabled());
  fireEvent.click(apply);
  await waitFor(() => expect(state.save).toHaveBeenCalledWith({ color_favorites: best, color_avoid: avoid }));
});
