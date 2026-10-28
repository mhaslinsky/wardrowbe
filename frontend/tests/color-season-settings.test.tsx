import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SettingsPage from '@/app/dashboard/settings/page'

const state = vi.hoisted(() => ({
  preferences: { standing_rules: ['Never a polo under an overshirt'] } as { standing_rules: string[]; color_season?: 'cool-winter' | null } | undefined,
  save: vi.fn().mockResolvedValue({}),
}))
vi.mock('@/lib/hooks/use-preferences', () => ({
  usePreferences: () => ({ data: state.preferences, isLoading: false }),
  useUpdatePreferences: () => ({ mutateAsync: state.save, isPending: false }),
  useResetPreferences: () => ({ mutateAsync: vi.fn() }),
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
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

beforeEach(() => {
  state.save.mockClear()
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
