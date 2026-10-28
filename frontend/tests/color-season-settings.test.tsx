import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SettingsPage from '@/app/dashboard/settings/page'

const state = vi.hoisted(() => ({
  preferences: { standing_rules: ['Never a polo under an overshirt'] } as { standing_rules: string[] } | undefined,
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
    style_profile: expect.objectContaining({ color_season: 'cool-winter', casual: 50 }),
  })))
  state.save.mockClear()
  fireEvent.change(select, { target: { value: '' } })
  fireEvent.click(screen.getAllByText('save', { selector: 'button' })[0])
  await waitFor(() => expect(state.save).toHaveBeenCalledWith(expect.objectContaining({
    style_profile: expect.objectContaining({ color_season: null, casual: 50 }),
  })))
})
