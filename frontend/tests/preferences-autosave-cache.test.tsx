import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import { useUpdatePreferences } from '@/lib/hooks/use-preferences';
import { queryKeys } from '@/lib/hooks/query-keys';
import type { Preferences } from '@/lib/types';

vi.mock('@/lib/api', () => ({ api: { patch: vi.fn() } }));

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
