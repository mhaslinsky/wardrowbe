import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';
import { useInspiration } from '@/lib/hooks/use-inspiration';
import { setAccessToken } from '@/lib/api';

const savedLook = { id: 'look1', image_url: '/look.webp', slots: [], items: [] };
let queryClient: QueryClient;
beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  setAccessToken(null);
  vi.mocked(fetch).mockReset();
});
function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
it('loads saved looks, including an empty wardrobe match', async () => {
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify([savedLook])));
  const { result } = renderHook(() => useInspiration(), { wrapper });
  await waitFor(() => expect(result.current.looks.isSuccess).toBe(true));
  expect(result.current.looks.data).toEqual([savedLook]);
  expect(fetch).toHaveBeenCalledWith('/api/v1/inspiration', expect.objectContaining({ method: 'GET', credentials: 'include' }));
});
it('uploads the image with authentication and refreshes saved looks and outfits', async () => {
  vi.mocked(fetch).mockImplementation(async (_url, options) => new Response(JSON.stringify(options?.method === 'POST' ? savedLook : [])));
  setAccessToken('test-token');
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  const { result } = renderHook(() => useInspiration(), { wrapper });
  await waitFor(() => expect(result.current.looks.isSuccess).toBe(true));
  const photo = new File(['photo'], 'look.png', { type: 'image/png' });
  await act(async () => { await result.current.create.mutateAsync(photo); });
  const upload = vi.mocked(fetch).mock.calls.find(([, options]) => options?.method === 'POST');
  expect(upload?.[0]).toBe('/api/v1/inspiration');
  expect(upload?.[1]).toMatchObject({ credentials: 'include', headers: { Authorization: 'Bearer test-token' } });
  expect((upload?.[1]?.body as FormData).get('image')).toBe(photo);
  await waitFor(() => expect(result.current.create.data).toEqual(savedLook));
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['inspiration'] });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['outfits'] });
});
it.each([400, 413, 415, 422, 500])('rejects failed uploads (%s) without claiming success', async (status) => {
  vi.mocked(fetch).mockImplementation(async (_url, options) => options?.method === 'POST'
    ? new Response(JSON.stringify({ detail: 'Invalid image' }), { status })
    : new Response('[]'));
  const { result } = renderHook(() => useInspiration(), { wrapper });
  await waitFor(() => expect(result.current.looks.isSuccess).toBe(true));
  await act(async () => {
    await expect(result.current.create.mutateAsync(new File(['bad'], 'look.jpg'))).rejects.toThrow();
  });
  expect(result.current.create.isSuccess).toBe(false);
  await waitFor(() => expect(result.current.create.isError).toBe(true));
});
it('deletes a saved look and refreshes both lists', async () => {
  vi.mocked(fetch).mockImplementation(async (_url, options) => options?.method === 'DELETE'
    ? new Response(null, { status: 204 }) : new Response(JSON.stringify([savedLook])));
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  const { result } = renderHook(() => useInspiration(), { wrapper });
  await waitFor(() => expect(result.current.looks.isSuccess).toBe(true));
  await act(async () => { await result.current.remove.mutateAsync('look1'); });
  expect(fetch).toHaveBeenCalledWith('/api/v1/inspiration/look1', expect.objectContaining({ method: 'DELETE' }));
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['inspiration'] });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['outfits'] });
});
