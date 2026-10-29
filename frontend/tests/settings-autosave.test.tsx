import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import SettingsPage from '@/app/dashboard/settings/page';
import type { UserProfile } from '@/lib/hooks/use-user';
import type { Preferences } from '@/lib/types';

const state = vi.hoisted(() => ({
  preferences: {} as Partial<Preferences>,
  persisted: {} as Partial<Preferences>,
  save: vi.fn(),
  reset: vi.fn(),
  profile: undefined as Partial<UserProfile> | undefined,
  profileSave: vi.fn(),
}));
vi.mock('@/lib/hooks/use-preferences', () => ({
  usePreferences: () => ({ data: state.preferences, isLoading: false }),
  useUpdatePreferences: () => ({ mutateAsync: state.save }),
  useResetPreferences: () => ({ mutateAsync: state.reset }),
  useTestAIEndpoint: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock('@/lib/hooks/use-user', () => ({
  useUserProfile: () => ({ data: state.profile, isLoading: false }),
  useUpdateUserProfile: () => ({ mutateAsync: state.profileSave }),
}));
vi.mock('@/components/ui/select', () => ({
  Select: ({ value, onValueChange, children }: { value: string; onValueChange: (value: string) => void; children: React.ReactNode }) => <select value={value} onChange={(event) => onValueChange(event.target.value)}>{children}</select>,
  SelectContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  SelectItem: ({ value, children }: { value: string; children: React.ReactNode }) => <option value={value}>{children}</option>,
  SelectTrigger: () => null,
  SelectValue: () => null,
}));
vi.mock('@/components/preview-photo-settings', () => ({ PreviewPhotoSettings: () => null }));
vi.mock('@/components/access-token-settings', () => ({ AccessTokenSettings: () => null }));
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

beforeEach(() => {
  state.preferences = { standing_rules: ['Original'], color_season: null, temperature_unit: 'celsius', variety_level: 'moderate', preview_preferences: { lines: [], tuck: 'outfit', rev: 'rev' } };
  state.persisted = { ...state.preferences };
  state.save.mockReset().mockImplementation(async (patch) => {
    state.persisted = { ...state.persisted, ...patch };
    return { ...state.persisted };
  });
  state.profile = undefined;
  state.profileSave.mockReset().mockImplementation(async (patch) => ({ ...state.profile, ...patch }));
  state.reset.mockReset().mockResolvedValue(state.preferences);
  vi.stubGlobal('localStorage', { getItem: vi.fn(), setItem: vi.fn() });
});

afterEach(async () => {
  await act(async () => { cleanup(); });
  vi.unstubAllGlobals();
});

function selectFor(text: string) {
  return screen.getByText(text).parentElement!.querySelector('select')!;
}

it('saves different controls independently and removes the Save button', async () => {
  render(<SettingsPage />);
  fireEvent.change(selectFor('temperature.unit'), { target: { value: 'fahrenheit' } });
  fireEvent.change(selectFor('recommendations.varietyLevel'), { target: { value: 'high' } });
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(2));
  expect(state.save.mock.calls.map(([patch]) => patch)).toEqual([{ temperature_unit: 'fahrenheit' }, { variety_level: 'high' }]);
  expect(state.persisted).toMatchObject({ temperature_unit: 'fahrenheit', variety_level: 'high' });
  expect(selectFor('temperature.unit')).toHaveValue('fahrenheit');
  expect(selectFor('recommendations.varietyLevel')).toHaveValue('high');
  expect(screen.queryByRole('button', { name: 'save' })).not.toBeInTheDocument();
  expect(await screen.findByRole('status')).toHaveTextContent('autosave.saved');
});

it('queues same-field changes until the older response finishes and protects them from refetches', async () => {
  let finishFirst!: (value: unknown) => void;
  state.save.mockImplementationOnce(() => new Promise((resolve) => { finishFirst = resolve; }));
  const { rerender } = render(<SettingsPage />);
  const select = selectFor('temperature.unit');
  fireEvent.change(select, { target: { value: 'fahrenheit' } });
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1));
  fireEvent.change(select, { target: { value: 'celsius' } });
  state.preferences = { ...state.preferences, temperature_unit: 'fahrenheit' };
  rerender(<SettingsPage />);
  expect(select).toHaveValue('celsius');
  await act(async () => {
    state.persisted = { ...state.persisted, temperature_unit: 'fahrenheit' };
    finishFirst({ ...state.persisted });
  });
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(2));
  expect(state.save.mock.calls[1][0]).toEqual({ temperature_unit: 'celsius' });
  expect(state.persisted.temperature_unit).toBe('celsius');
  expect(select).toHaveValue('celsius');
});

it('reverts a failed save to the last successful value and reports failure', async () => {
  render(<SettingsPage />);
  const select = selectFor('temperature.unit');
  fireEvent.change(select, { target: { value: 'fahrenheit' } });
  await screen.findByText('autosave.saved');
  state.save.mockRejectedValueOnce(new Error('offline'));
  fireEvent.change(select, { target: { value: 'celsius' } });
  expect(await screen.findByRole('alert')).toHaveTextContent('autosave.error');
  expect(select).toHaveValue('fahrenheit');
});

it('saves text only on blur, preserving a draft across a preferences refresh', async () => {
  const { rerender } = render(<SettingsPage />);
  const editor = screen.getByLabelText('standingRules.label');
  fireEvent.change(editor, { target: { value: '  New rule  \n\n Another rule' } });
  state.preferences = { ...state.preferences, variety_level: 'high' };
  rerender(<SettingsPage />);
  expect(editor).toHaveValue('  New rule  \n\n Another rule');
  fireEvent.blur(editor);
  await waitFor(() => expect(state.save).toHaveBeenCalledWith({ standing_rules: ['New rule', 'Another rule'] }));
});

it.each([Array(21).fill('rule').join('\n'), 'x'.repeat(301)])('blocks invalid standing rules and names the limit', async (value) => {
  render(<SettingsPage />);
  fireEvent.change(screen.getByLabelText('standingRules.label'), { target: { value } });
  fireEvent.blur(screen.getByLabelText('standingRules.label'));
  expect(await screen.findByText('standingRules.limit')).toBeInTheDocument();
  fireEvent.change(selectFor('temperature.unit'), { target: { value: 'fahrenheit' } });
  await waitFor(() => expect(state.save).toHaveBeenCalledWith({ temperature_unit: 'fahrenheit' }));
  expect(state.save).toHaveBeenCalledTimes(1);
});

it('keeps Reset confirmation', async () => {
  const confirm = vi.fn(() => false);
  vi.stubGlobal('confirm', confirm);
  render(<SettingsPage />);
  fireEvent.click(screen.getByRole('button', { name: 'reset' }));
  expect(confirm).toHaveBeenCalledWith('confirmReset');
  expect(state.reset).not.toHaveBeenCalled();
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole('button', { name: 'reset' }));
  await waitFor(() => expect(state.reset).toHaveBeenCalledWith('rev'));
});

it('saves a slider on release with the other style values intact', async () => {
  state.preferences = { ...state.preferences, style_profile: { casual: 50, formal: 20, sporty: 30, minimalist: 40, bold: 10 } };
  render(<SettingsPage />);
  fireEvent.keyDown(screen.getAllByRole('slider')[0], { key: 'ArrowRight' });
  await waitFor(() => expect(state.save).toHaveBeenCalledWith({ style_profile: { casual: 60, formal: 20, sporty: 30, minimalist: 40, bold: 10 } }));
});

it('restores failed standing rules and accepts an empty rule list', async () => {
  state.save.mockRejectedValueOnce(new Error('offline'));
  render(<SettingsPage />);
  const editor = screen.getByLabelText('standingRules.label');
  fireEvent.change(editor, { target: { value: 'Changed' } });
  fireEvent.blur(editor);
  await screen.findByRole('alert');
  expect(editor).toHaveValue('Original');
  fireEvent.change(editor, { target: { value: '' } });
  fireEvent.blur(editor);
  await waitFor(() => expect(state.save).toHaveBeenLastCalledWith({ standing_rules: [] }));
  expect(editor).toHaveValue('');
});

function inputFor(text: string) {
  return screen.getByText(text).parentElement!.querySelector('input')!;
}

it('saves each location field on blur and the timezone on selection', async () => {
  state.profile = { location_name: 'London', location_lat: 51.5, location_lon: -0.1, timezone: 'UTC' };
  render(<SettingsPage />);
  const city = inputFor('location.cityLabel');
  fireEvent.change(city, { target: { value: 'Paris' } });
  fireEvent.blur(city);
  fireEvent.change(selectFor('location.timezone'), { target: { value: 'Europe/Paris' } });
  await waitFor(() => expect(state.profileSave).toHaveBeenCalledTimes(2));
  expect(state.profileSave.mock.calls.map(([patch]) => patch)).toEqual([{ location_name: 'Paris' }, { timezone: 'Europe/Paris' }]);
  expect(city).toHaveValue('Paris');
});

it.each(['', '91'])('blocks invalid latitude (%s) while other profile fields still save', async (value) => {
  state.profile = { location_lat: 51.5, location_lon: -0.1, timezone: 'UTC' };
  render(<SettingsPage />);
  fireEvent.change(inputFor('location.latitude'), { target: { value } });
  fireEvent.blur(inputFor('location.latitude'));
  expect(screen.getByText(value ? 'location.errors.latitudeRange' : 'location.errors.invalidCoordinates')).toBeInTheDocument();
  fireEvent.change(selectFor('location.timezone'), { target: { value: 'Europe/Paris' } });
  await waitFor(() => expect(state.profileSave).toHaveBeenCalledTimes(1));
  expect(state.profileSave).toHaveBeenCalledWith({ timezone: 'Europe/Paris' });
});

it('rolls back a failed profile field to its saved value', async () => {
  state.profile = { location_name: 'London', timezone: 'UTC' };
  state.profileSave.mockRejectedValueOnce(new Error('offline'));
  render(<SettingsPage />);
  fireEvent.change(inputFor('location.cityLabel'), { target: { value: 'Paris' } });
  fireEvent.blur(inputFor('location.cityLabel'));
  await screen.findByRole('alert');
  expect(inputFor('location.cityLabel')).toHaveValue('London');
});

it('saves and clears measurements on blur, preserving other measurements', async () => {
  state.profile = { body_measurements: { height: 178, chest: 96 } };
  render(<SettingsPage />);
  const height = inputFor('body.fields.height');
  fireEvent.change(height, { target: { value: '180' } });
  fireEvent.blur(height);
  await waitFor(() => expect(state.profileSave).toHaveBeenCalledWith({ body_measurements: { height: 180, chest: 96 } }));
  fireEvent.change(height, { target: { value: '' } });
  fireEvent.blur(height);
  await waitFor(() => expect(state.profileSave).toHaveBeenLastCalledWith({ body_measurements: { chest: 96 } }));
  expect(height).toHaveValue(null);
});

it('keeps converted measurement units when a save fails', async () => {
  state.profile = { body_measurements: { height: 178 } };
  state.profileSave.mockRejectedValueOnce(new Error('offline'));
  render(<SettingsPage />);
  fireEvent.click(screen.getByRole('button', { name: 'body.metric' }));
  const height = inputFor('body.fields.height');
  expect(height).toHaveValue(70.1);
  fireEvent.change(height, { target: { value: '72' } });
  fireEvent.blur(height);
  await screen.findByRole('alert');
  expect(height).toHaveValue(70.1);
});

it('R1-1: tabbing through empty coordinates and unchanged imperial measurements sends nothing', async () => {
  state.profile = { body_measurements: { height: 178 } };
  render(<SettingsPage />);
  fireEvent.click(screen.getByRole('button', { name: 'body.metric' }));
  fireEvent.blur(inputFor('body.fields.height'));
  fireEvent.blur(inputFor('location.latitude'));
  fireEvent.blur(inputFor('location.longitude'));
  fireEvent.blur(screen.getByLabelText('standingRules.label'));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  await act(async () => { await Promise.resolve(); });
  expect(state.profileSave).not.toHaveBeenCalled();
  expect(state.save).not.toHaveBeenCalled();
});

it('R1-2: warns before unloading a staged draft and saves it on in-app unmount', async () => {
  const { unmount } = render(<SettingsPage />);
  fireEvent.change(screen.getByLabelText('standingRules.label'), { target: { value: '  Keep this rule  ' } });
  const leave = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(leave);
  expect(leave.defaultPrevented).toBe(true);
  unmount();
  await waitFor(() => expect(state.save).toHaveBeenCalledWith({ standing_rules: ['Keep this rule'] }));
});

it('R1-4: late reverse geocoding never replays coordinates over a newer edit', async () => {
  state.profile = { location_lat: 1, location_lon: 2, location_name: 'Original' };
  let locate!: PositionCallback;
  vi.stubGlobal('navigator', { geolocation: { getCurrentPosition: vi.fn((success) => { locate = success; }) } });
  let finishLookup!: (value: Response) => void;
  vi.mocked(fetch).mockImplementationOnce(() => new Promise((resolve) => { finishLookup = resolve; }));
  render(<SettingsPage />);
  fireEvent.click(screen.getByRole('button', { name: 'location.useMyLocation' }));
  act(() => { locate({ coords: { latitude: 10, longitude: 20 } } as GeolocationPosition); });
  fireEvent.change(inputFor('location.latitude'), { target: { value: '30' } });
  fireEvent.blur(inputFor('location.latitude'));
  await act(async () => { finishLookup(new Response(JSON.stringify({ address: { city: 'Detected' } }))); });
  await waitFor(() => expect(state.profileSave).toHaveBeenCalled());
  expect(inputFor('location.latitude')).toHaveValue(30);
  expect(state.profileSave.mock.calls.at(-1)?.[0]).toEqual({ location_lat: 30, location_lon: 20 });
});

it('R1-6: Reset shows one error with the server reason', async () => {
  const { toast } = await import('sonner');
  vi.mocked(toast.error).mockClear();
  state.reset.mockRejectedValueOnce(new Error('Preferences changed'));
  vi.stubGlobal('confirm', vi.fn(() => true));
  render(<SettingsPage />);
  fireEvent.click(screen.getByRole('button', { name: 'reset' }));
  await waitFor(() => expect(toast.error).toHaveBeenCalledExactlyOnceWith('Preferences changed'));
});

it('keeps a saved measurement when units change before the profile refetch', async () => {
  state.profile = { body_measurements: { height: 178, chest: 96 } };
  render(<SettingsPage />);
  fireEvent.change(inputFor('body.fields.height'), { target: { value: '180' } });
  fireEvent.blur(inputFor('body.fields.height'));
  await screen.findByText('autosave.saved');
  fireEvent.click(screen.getByRole('button', { name: 'body.metric' }));
  expect(inputFor('body.fields.height')).toHaveValue(70.9);
  fireEvent.change(inputFor('body.fields.chest'), { target: { value: '40' } });
  fireEvent.blur(inputFor('body.fields.chest'));
  await waitFor(() => expect(state.profileSave).toHaveBeenCalledTimes(2));
  expect(state.profileSave.mock.calls[1][0].body_measurements.height).toBeCloseTo(180, 0);
});

it('clears the rules error when the editor returns to its saved value', () => {
  render(<SettingsPage />);
  const editor = screen.getByLabelText('standingRules.label');
  fireEvent.change(editor, { target: { value: 'x'.repeat(301) } });
  fireEvent.blur(editor);
  expect(screen.getByText('standingRules.limit')).toBeInTheDocument();
  fireEvent.change(editor, { target: { value: 'Original' } });
  fireEvent.blur(editor);
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('clears invalid coordinate errors when location detection supplies valid coordinates', async () => {
  state.profile = { location_lat: 1, location_lon: 2 };
  let locate!: PositionCallback;
  vi.stubGlobal('navigator', { geolocation: { getCurrentPosition: vi.fn((success) => { locate = success; }) } });
  vi.mocked(fetch).mockResolvedValueOnce(new Response('{}'));
  render(<SettingsPage />);
  fireEvent.change(inputFor('location.latitude'), { target: { value: '91' } });
  fireEvent.blur(inputFor('location.latitude'));
  expect(screen.getByText('location.errors.latitudeRange')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'location.useMyLocation' }));
  await act(async () => { locate({ coords: { latitude: 10, longitude: 20 } } as GeolocationPosition); });
  await waitFor(() => expect(state.profileSave).toHaveBeenCalled());
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('does not save a reverse-geocoded city when its coordinates fail to save', async () => {
  state.profile = { location_lat: 1, location_lon: 2, location_name: 'Original' };
  let locate!: PositionCallback;
  let rejectCoordinates!: (error: Error) => void;
  state.profileSave.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectCoordinates = reject; }));
  vi.stubGlobal('navigator', { geolocation: { getCurrentPosition: vi.fn((success) => { locate = success; }) } });
  vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({ address: { city: 'Detected' } })));
  render(<SettingsPage />);
  fireEvent.click(screen.getByRole('button', { name: 'location.useMyLocation' }));
  await act(async () => { locate({ coords: { latitude: 10, longitude: 20 } } as GeolocationPosition); });
  await act(async () => { rejectCoordinates(new Error('Offline')); });
  expect(state.profileSave).toHaveBeenCalledTimes(1);
  expect(inputFor('location.cityLabel')).toHaveValue('Original');
});

it('shows an ordinary save failure reason once while keeping the short status', async () => {
  const { toast } = await import('sonner');
  vi.mocked(toast.error).mockClear();
  state.save.mockRejectedValueOnce(new Error('Preferences changed'));
  render(<SettingsPage />);
  fireEvent.change(selectFor('temperature.unit'), { target: { value: 'fahrenheit' } });
  expect(await screen.findByRole('alert')).toHaveTextContent('autosave.error');
  expect(toast.error).toHaveBeenCalledExactlyOnceWith('Preferences changed');
});

it('saves valid rules after a limit error and warns before leaving an invalid draft', async () => {
  const confirm = vi.fn(() => false);
  vi.stubGlobal('confirm', confirm);
  render(<SettingsPage />);
  const editor = screen.getByLabelText('standingRules.label');
  fireEvent.change(editor, { target: { value: 'x'.repeat(301) } });
  fireEvent.blur(editor);
  fireEvent.change(editor, { target: { value: 'A valid rule' } });
  fireEvent.blur(editor);
  await waitFor(() => expect(state.save).toHaveBeenCalledWith({ standing_rules: ['A valid rule'] }));
  fireEvent.change(editor, { target: { value: 'x'.repeat(301) } });
  const navigate = new MouseEvent('click', { bubbles: true, cancelable: true });
  screen.getByRole('link', { name: 'fitProfile' }).dispatchEvent(navigate);
  expect(confirm).toHaveBeenCalledWith('confirmLeave');
  expect(navigate.defaultPrevented).toBe(true);
});

it('keeps a unit-toggle click made while the blur save is pending', async () => {
  state.profile = { body_measurements: { height: 178 } };
  let finish!: (value: unknown) => void;
  state.profileSave.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  render(<SettingsPage />);
  fireEvent.change(inputFor('body.fields.height'), { target: { value: '180' } });
  fireEvent.blur(inputFor('body.fields.height'));
  fireEvent.click(screen.getByRole('button', { name: 'body.metric' }));
  await act(async () => { finish({ body_measurements: { height: 180 } }); });
  expect(screen.getByRole('button', { name: 'body.imperial' })).toBeEnabled();
  expect(inputFor('body.fields.height')).toHaveValue(70.9);
});

it('combines both resource statuses with saving before errors and one final Saved', async () => {
  state.profile = { location_name: 'London' };
  state.save.mockRejectedValueOnce(new Error('Offline'));
  render(<SettingsPage />);
  fireEvent.change(selectFor('temperature.unit'), { target: { value: 'fahrenheit' } });
  await screen.findByRole('alert');
  let finish!: (value: unknown) => void;
  state.profileSave.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  fireEvent.change(inputFor('location.cityLabel'), { target: { value: 'Paris' } });
  fireEvent.blur(inputFor('location.cityLabel'));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('autosave.saving');
  await act(async () => { finish({ location_name: 'Paris' }); });
  expect(screen.getByRole('alert')).toHaveTextContent('autosave.error');
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  fireEvent.change(selectFor('temperature.unit'), { target: { value: 'fahrenheit' } });
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('autosave.saved'));
  expect(screen.getAllByRole('status')).toHaveLength(1);
});

it('validates coordinates together and saves a valid pair in one PATCH', async () => {
  state.profile = { location_lat: 1, location_lon: 2 };
  render(<SettingsPage />);
  fireEvent.change(inputFor('location.latitude'), { target: { value: '3' } });
  fireEvent.change(inputFor('location.longitude'), { target: { value: '400' } });
  fireEvent.blur(inputFor('location.latitude'));
  expect(screen.getByText('location.errors.longitudeRange')).toBeInTheDocument();
  expect(state.profileSave).not.toHaveBeenCalled();
  fireEvent.change(inputFor('location.longitude'), { target: { value: '4' } });
  fireEvent.blur(inputFor('location.longitude'));
  await waitFor(() => expect(state.profileSave).toHaveBeenCalledWith({ location_lat: 3, location_lon: 4 }));
});

it('retains an invalid rules draft without sending it when a queued rule is edited', async () => {
  let finish!: (value: unknown) => void;
  state.save.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  render(<SettingsPage />);
  fireEvent.change(selectFor('temperature.unit'), { target: { value: 'fahrenheit' } });
  const editor = screen.getByLabelText('standingRules.label');
  fireEvent.change(editor, { target: { value: 'Queued rule' } });
  fireEvent.blur(editor);
  fireEvent.change(editor, { target: { value: 'x'.repeat(301) } });
  fireEvent.blur(editor);
  await act(async () => { finish({ ...state.preferences, temperature_unit: 'fahrenheit' }); });
  expect(state.save).toHaveBeenCalledTimes(1);
  expect(editor).toHaveValue('x'.repeat(301));
  expect(screen.getByText('standingRules.limit')).toBeInTheDocument();
  fireEvent.change(editor, { target: { value: 'Valid latest rule' } });
  fireEvent.blur(editor);
  await waitFor(() => expect(state.save).toHaveBeenLastCalledWith({ standing_rules: ['Valid latest rule'] }));
});
