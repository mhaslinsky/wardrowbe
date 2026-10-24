import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import { ReactNode } from 'react';
import { expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import { Item } from '@/lib/types';
import { useChoosePhoto, useRedrawPhoto } from '@/lib/hooks/use-items';
vi.mock('@/lib/api', async (original) => ({ ...(await original<typeof import('@/lib/api')>()), api: { post: vi.fn() } }));
it.each(['redraw', 'switch'] as const)('updates every cached image after %s without a retry', async (action) => {
  const client = new QueryClient();
  const old = { id: 'one', image_url: '/old' } as Item;
  const updated = { ...old, image_url: '/new' };
  client.setQueryData(['items', 'page'], { items: [old] });
  vi.mocked(api.post).mockResolvedValue(updated);
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const { result } = renderHook(() => ({ redraw: useRedrawPhoto(), choose: useChoosePhoto() }), { wrapper });
  await act(async () => {
    if (action === 'redraw') await result.current.redraw.mutateAsync({ id: 'one', retry: true });
    else await result.current.choose.mutateAsync({ id: 'one', choice: 'original' });
  });
  expect(api.post).toHaveBeenLastCalledWith(action === 'redraw' ? '/items/one/redraw-photo' : '/items/one/photo/original', ...(action === 'redraw' ? [{ retry: true }] : []));
  expect(client.getQueryData(['item', 'one'])).toEqual(updated);
  expect(client.getQueryData(['items', 'page'])).toEqual({ items: [updated] });
  expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual(expect.arrayContaining([['items'], ['item', 'one'], ['outfits'], ['calendarOutfits']]));
  client.clear();
});
