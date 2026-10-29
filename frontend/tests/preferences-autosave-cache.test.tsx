import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import { useUpdatePreferences } from '@/lib/hooks/use-preferences';
import { queryKeys } from '@/lib/hooks/query-keys';
import type { Preferences } from '@/lib/types';

vi.mock('@/lib/api', async (original) => ({ ...await original<typeof import('@/lib/api')>(), api: { patch: vi.fn(), post: vi.fn() } }));
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }));

it('merges only submitted fields from a full save response into the cache', async () => {
  const client = new QueryClient();
  client.setQueryData(queryKeys.preferences, { temperature_unit: 'fahrenheit', variety_level: 'moderate' });
  vi.mocked(api.patch).mockResolvedValue({ temperature_unit: 'celsius', variety_level: 'high' });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const { result } = renderHook(() => useUpdatePreferences(), { wrapper });
  await act(async () => { await result.current.mutateAsync({ variety_level: 'high' }); });
  expect(client.getQueryData<Preferences>(queryKeys.preferences)).toEqual({ temperature_unit: 'fahrenheit', variety_level: 'high' });
  client.clear();
});

it.each(['update', 'reset'] as const)('R1-6: %s delegates error feedback to the caller', async (kind) => {
  const { createQueryClient } = await import('@/lib/query-client');
  const { ApiError } = await import('@/lib/api');
  const { useResetPreferences } = await import('@/lib/hooks/use-preferences');
  const { toast } = await import('sonner');
  vi.mocked(toast.error).mockClear();
  const client = createQueryClient();
  const failure = new ApiError('Preferences changed', 409, {});
  vi.mocked(kind === 'update' ? api.patch : api.post).mockRejectedValueOnce(failure);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const { result } = renderHook(() => ({ update: useUpdatePreferences({ toastsOwnErrors: true }), reset: useResetPreferences({ toastsOwnErrors: true }) }), { wrapper });
  await act(async () => {
    await expect(kind === 'update' ? result.current.update.mutateAsync({ temperature_unit: 'fahrenheit' }) : result.current.reset.mutateAsync('rev')).rejects.toThrow('Preferences changed');
  });
  expect(toast.error).not.toHaveBeenCalled();
  client.clear();
});

it('writes confirmed profile fields into the cache without replacing unrelated fields', async () => {
  const { useUpdateUserProfile } = await import('@/lib/hooks/use-user');
  const client = new QueryClient();
  client.setQueryData(queryKeys.userProfile, { body_measurements: { height: 178 }, timezone: 'UTC' });
  vi.mocked(api.patch).mockResolvedValueOnce({ body_measurements: { height: 180 }, timezone: 'Europe/London' });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const { result } = renderHook(() => useUpdateUserProfile(), { wrapper });
  await act(async () => { await result.current.mutateAsync({ body_measurements: { height: 180 } }); });
  expect(client.getQueryData(queryKeys.userProfile)).toEqual({ body_measurements: { height: 180 }, timezone: 'UTC' });
  client.clear();
});
