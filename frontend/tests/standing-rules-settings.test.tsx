import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsPage from '@/app/dashboard/settings/page'

const state = vi.hoisted(() => ({
  preferences: { standing_rules: ['Never a polo under an overshirt'] } as { standing_rules: string[] } | undefined,
  save: vi.fn().mockImplementation(async (patch) => patch),
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
    fireEvent.blur(screen.getByLabelText('standingRules.label'))
    await waitFor(() => expect(state.save).toHaveBeenCalledWith({ standing_rules: ['Add a base shirt', 'Never a polo'] }))
  })

  it.each(['', Array(21).fill('rule').join('\n'), 'x'.repeat(301)])('handles empty or over-limit input', async (value) => {
    render(<SettingsPage />)
    fireEvent.change(screen.getByLabelText('standingRules.label'), { target: { value } })
    fireEvent.blur(screen.getByLabelText('standingRules.label'))
    if (value === '') {
      await waitFor(() => expect(state.save).toHaveBeenCalledWith({ standing_rules: [] }))
    } else {
      expect(screen.getByText('standingRules.limit')).toBeInTheDocument()
      expect(state.save).not.toHaveBeenCalled()
    }
  })
})

vi.mock('@/components/preview-photo-settings', () => ({ PreviewPhotoSettings: () => null }))

it('keeps the main Settings draft when preview preferences refresh', async () => {
  const original = state.preferences;
  if (!original) throw new Error('Expected seeded preferences');
  try {
    const { rerender } = render(<SettingsPage />);
    const editor = screen.getByLabelText('standingRules.label');
    fireEvent.change(editor, { target: { value: 'Keep this unsaved rule' } });
    state.preferences = { ...original, standing_rules: [...original.standing_rules] };
    rerender(<SettingsPage />);
    expect(editor).toHaveValue('Keep this unsaved rule');
    expect(screen.queryByRole('button', { name: 'save' })).not.toBeInTheDocument();
  } finally { state.preferences = original; }
});


it.each([false, true])('omits unseeded standing rules when saving another field (late load: %s)', async (lateLoad) => {
  const original = state.preferences;
  try {
    state.preferences = undefined;
    const { rerender } = render(<SettingsPage />);
    const input = screen.getByText('recommendations.avoidRepeatDays').parentElement!.querySelector('input')!;
    fireEvent.change(input, { target: { value: '4' } });
    if (lateLoad) {
      state.preferences = original;
      rerender(<SettingsPage />);
    }
    fireEvent.blur(input);
    await waitFor(() => expect(state.save).toHaveBeenCalledWith({ avoid_repeat_days: 4 }));
  } finally { state.preferences = original; }
});

it('allows explicitly clearing rules before the server data arrives', async () => {
  const original = state.preferences;
  try {
    state.preferences = undefined;
    render(<SettingsPage />);
    fireEvent.change(screen.getByLabelText('standingRules.label'), { target: { value: '   ' } });
    fireEvent.blur(screen.getByLabelText('standingRules.label'));
    await waitFor(() => expect(state.save).toHaveBeenCalledWith({ standing_rules: [] }));
  } finally { state.preferences = original; }
});

vi.mock('@/components/fit-profile-settings', () => ({ FitProfileSettings: () => null }));
vi.mock('@/components/access-token-settings', () => ({ AccessTokenSettings: () => null }));
