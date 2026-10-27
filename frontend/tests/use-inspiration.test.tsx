import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';
import { useInspiration } from '@/lib/hooks/use-inspiration';
import { createQueryClient } from '@/lib/query-client';
import { sessionMock } from './setup';
import savedLook from './fixtures/inspiration-look.json';
import { ApiError, setAccessToken } from '@/lib/api';

let queryClient: ReturnType<typeof createQueryClient>;
beforeEach(() => {
  queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  sessionMock.status = 'unauthenticated';
  sessionMock.data = null;
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
it.each([413, 429, 503])('rejects failed uploads (%s) without claiming success', async (status) => {
  vi.mocked(fetch).mockImplementation(async (_url, options) => options?.method === 'POST'
    ? new Response(JSON.stringify({ detail: 'Invalid image' }), { status })
    : new Response('[]'));
  const { result } = renderHook(() => useInspiration(), { wrapper });
  await waitFor(() => expect(result.current.looks.isSuccess).toBe(true));
  await act(async () => {
    await expect(result.current.create.mutateAsync(new File(['bad'], 'look.jpg'))).rejects.toMatchObject({ name: 'ApiError', status, data: { detail: 'Invalid image' } });
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

it('waits for session resolution before loading with the session token', async () => {
  sessionMock.status = 'loading';
  const requestSessions: string[] = [];
  vi.mocked(fetch).mockImplementation(async () => {
    requestSessions.push(sessionMock.status);
    return new Response(JSON.stringify([savedLook]));
  });
  const { result, rerender } = renderHook(() => useInspiration(), { wrapper });
  expect(result.current.looks.fetchStatus).toBe('idle');
  sessionMock.status = 'authenticated';
  sessionMock.data = { accessToken: 'session-token', expires: '2099-01-01', user: {} };
  rerender();
  await waitFor(() => expect(result.current.looks.isSuccess).toBe(true));
  expect(requestSessions).toEqual(['authenticated']);
  expect(fetch).toHaveBeenCalledWith('/api/v1/inspiration', expect.objectContaining({
    headers: expect.objectContaining({ Authorization: 'Bearer session-token' }),
  }));
});

it('keeps the HTTP status when an upload error body is malformed', async () => {
  vi.mocked(fetch).mockImplementation(async (_url, options) => options?.method === 'POST'
    ? new Response('not JSON', { status: 503 }) : new Response('[]'));
  const { result } = renderHook(() => useInspiration(), { wrapper });
  await waitFor(() => expect(result.current.looks.isSuccess).toBe(true));
  await act(async () => {
    await expect(result.current.create.mutateAsync(new File(['photo'], 'look.jpg')))
      .rejects.toBeInstanceOf(ApiError);
  });
  await waitFor(() => expect(result.current.create.isError).toBe(true));
  expect(result.current.create.error).toMatchObject({ status: 503 });
});
