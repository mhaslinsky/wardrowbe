import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { AccessTokenSettings } from '@/components/access-token-settings';

const state = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn(), copy: vi.fn(), dateTime: vi.fn(), email: 'test@example.com' as string | null | undefined }));
vi.mock('@/lib/api', async (original) => ({ ...await original<typeof import('@/lib/api')>(), api: state }));
vi.mock('next-auth/react', () => ({ useSession: () => ({ status: 'authenticated', data: { user: { email: state.email } } }) }));
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key, useFormatter: () => ({ dateTime: state.dateTime }) }));
const token = { id: '492b28ee-ec8b-407b-94bf-7297efb00002', name: 'Extension', created_at: '2026-10-27T01:00:00Z', last_used_at: null };
function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>{children}</QueryClientProvider>;
}
beforeEach(() => {
  vi.resetAllMocks();
  state.email = 'test@example.com';
  state.dateTime.mockReturnValue('Oct 27');
  state.get.mockResolvedValue([]);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: state.copy } });
});
it('creates, copies and hides the secret, then revokes', async () => {
  state.post.mockResolvedValue({ ...token, token: 'wbt_test-secret' });
  render(<AccessTokenSettings />, { wrapper });
  await screen.findByText('empty');
  fireEvent.change(screen.getByLabelText('name'), { target: { value: 'Extension' } });
  state.get.mockResolvedValue([token]);
  fireEvent.click(screen.getByRole('button', { name: 'create' }));
  expect(await screen.findByLabelText('token')).toHaveValue('wbt_test-secret');
  fireEvent.click(screen.getByRole('button', { name: 'copy' }));
  await waitFor(() => expect(state.copy).toHaveBeenCalledWith('wbt_test-secret'));
  fireEvent.click(screen.getByRole('button', { name: 'dismiss' }));
  expect(screen.queryByLabelText('token')).not.toBeInTheDocument();
  expect(await screen.findByText('never')).toBeInTheDocument();
  state.get.mockResolvedValue([]);
  fireEvent.click(screen.getByRole('button', { name: 'revoke' }));
  await waitFor(() => expect(state.delete).toHaveBeenCalledWith(`/users/me/access-tokens/${token.id}`));
  expect(await screen.findByText('empty')).toBeInTheDocument();
});
it('reports a failed create without displaying a secret', async () => {
  state.post.mockRejectedValue(new Error('offline'));
  render(<AccessTokenSettings />, { wrapper });
  await screen.findByText('empty');
  fireEvent.change(screen.getByLabelText('name'), { target: { value: 'Extension' } });
  fireEvent.click(screen.getByRole('button', { name: 'create' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('failed');
  expect(screen.queryByLabelText('token')).not.toBeInTheDocument();
});
it('reports malformed token lists', async () => {
  state.get.mockResolvedValue({ wrong: true });
  render(<AccessTokenSettings />, { wrapper });
  expect(await screen.findByRole('alert')).toHaveTextContent('failed');
});

it.each([null, undefined])('shows the created secret without a session email (%s)', async (email) => {
  state.email = email;
  state.post.mockResolvedValue({ ...token, token: 'wbt_test-secret' });
  render(<AccessTokenSettings />, { wrapper });
  await screen.findByText('empty');
  fireEvent.change(screen.getByLabelText('name'), { target: { value: 'Extension' } });
  fireEvent.click(screen.getByRole('button', { name: 'create' }));
  expect(await screen.findByLabelText('token')).toHaveValue('wbt_test-secret');
});

it('lists the created token while the list refetch is pending', async () => {
  state.post.mockResolvedValue({ ...token, token: 'wbt_test-secret' });
  render(<AccessTokenSettings />, { wrapper });
  await screen.findByText('empty');
  let release: (value: typeof token[]) => void = () => {};
  state.get.mockImplementationOnce(() => new Promise<typeof token[]>((resolve) => { release = resolve; }));
  try {
    fireEvent.change(screen.getByLabelText('name'), { target: { value: 'Extension' } });
    fireEvent.click(screen.getByRole('button', { name: 'create' }));
    await screen.findByLabelText('token');
    expect(screen.getByRole('cell', { name: 'Extension' })).toBeInTheDocument();
    expect(screen.queryByText('empty')).not.toBeInTheDocument();
  } finally { release([token]); }
});

it('formats token dates in the browser time zone', async () => {
  const options = new Intl.DateTimeFormat().resolvedOptions();
  const zone = vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({ ...options, timeZone: 'America/New_York' });
  try {
    state.get.mockResolvedValue([token]);
    render(<AccessTokenSettings />, { wrapper });
    await screen.findByRole('cell', { name: 'Extension' });
    expect(state.dateTime).toHaveBeenCalledWith(expect.any(Date), expect.objectContaining({ timeZone: 'America/New_York' }));
  } finally { zone.mockRestore(); }
});
