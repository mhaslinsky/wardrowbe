import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsPage from '@/app/dashboard/settings/page'
import { toast } from 'sonner'

const state = vi.hoisted(() => ({
  preferences: { standing_rules: ['Never a polo under an overshirt'] },
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

describe('standing rules settings', () => {
  it('loads, trims and saves rules, skipping blank lines', async () => {
    render(<SettingsPage />)
    const editor = screen.getByLabelText('standingRules.label')
    expect(editor).toHaveValue('Never a polo under an overshirt')
    fireEvent.change(editor, { target: { value: '  Add a base shirt  \n\n Never a polo ' } })
    fireEvent.click(screen.getAllByText('save', { selector: 'button' })[0])
    await waitFor(() => expect(state.save).toHaveBeenCalledWith({ standing_rules: ['Add a base shirt', 'Never a polo'] }))
  })

  it.each(['', Array(21).fill('rule').join('\n'), 'x'.repeat(301)])('handles empty or over-limit input', async (value) => {
    render(<SettingsPage />)
    fireEvent.change(screen.getByLabelText('standingRules.label'), { target: { value } })
    fireEvent.click(screen.getAllByText('save', { selector: 'button' })[0])
    if (value === '') {
      await waitFor(() => expect(state.save).toHaveBeenCalledWith({ standing_rules: [] }))
    } else {
      expect(toast.error).toHaveBeenCalledWith('standingRules.limit')
      expect(state.save).not.toHaveBeenCalled()
    }
  })
})

vi.mock('@/components/preview-photo-settings', () => ({ PreviewPhotoSettings: () => null }))
