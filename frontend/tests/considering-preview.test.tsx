import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { ConsideringPreviewAction } from '@/components/considering-preview-action';
import { createQueryClient } from '@/lib/query-client';
import { sessionMock, translationMock } from './setup';

const client = createQueryClient();
let requests: { path: string; body: unknown }[];
let empty: boolean;
let fail: boolean;
vi.mock('@/components/outfit-preview', () => ({ OutfitPreview: ({ outfitId }: { outfitId: string }) => <div data-testid="preview">{outfitId}</div> }));
beforeEach(() => {
  translationMock.includeNamespace = true;
  sessionMock.status = 'authenticated';
  sessionMock.data = { accessToken: 'preview-token', expires: '2099-01-01', user: { email: 'preview@example.test' } };
  requests = []; empty = false; fail = false;
  vi.mocked(fetch).mockReset();
  vi.mocked(fetch).mockImplementation(async (url, options) => {
    const path = String(url);
    if (options?.method === 'POST') {
      requests.push({ path, body: JSON.parse(String(options.body)) });
      return new Response(JSON.stringify(fail ? { detail: 'Unavailable' } : { id: 'saved-outfit', items: [], unowned_item_ids: ['anchor'] }), { status: fail ? 422 : 201 });
    }
    if (path.includes('/outfits/')) return new Response(JSON.stringify({ id: 'saved-outfit' }));
    return new Response(JSON.stringify({ items: empty ? [] : [
      { id: 'dirty', name: 'Owned dirty shirt', status: 'ready', needs_wash: true },
      { id: 'processing', name: 'Processing shirt', status: 'processing' },
    ], total: empty ? 0 : 2, has_more: false }));
  });
});
afterEach(() => { client.clear(); translationMock.includeNamespace = false; sessionMock.data = null; });
const label = (key: string) => `wardrobe.considering.${key}`;
function open() {
  render(<QueryClientProvider client={client}><ConsideringPreviewAction itemId="anchor" /></QueryClientProvider>);
  fireEvent.click(screen.getByRole('button', { name: label('preview') }));
}
it('previews alone and reuses the saved outfit after cancel and reopen', async () => {
  open();
  fireEvent.click(await screen.findByRole('button', { name: label('previewAlone') }));
  expect(await screen.findByTestId('preview')).toHaveTextContent('saved-outfit');
  expect(requests[0].body).toEqual({ companion_ids: [] });
  fireEvent.click(screen.getByRole('button', { name: 'common.cancel' }));
  fireEvent.click(screen.getByRole('button', { name: label('preview') }));
  fireEvent.click(await screen.findByRole('button', { name: label('previewAlone') }));
  await screen.findByTestId('preview');
  expect(requests).toHaveLength(1);
});
it('offers owned dirty pieces, excludes unready pieces and submits selected IDs', async () => {
  open();
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Owned dirty shirt' }));
  expect(screen.queryByText('Processing shirt')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: label('previewSelected') }));
  await screen.findByTestId('preview');
  expect(requests[0].body).toEqual({ companion_ids: ['dirty'] });
  const url = vi.mocked(fetch).mock.calls.find(([path]) => String(path).includes('/items'))?.[0];
  expect(String(url)).not.toContain('needs_wash');
});
it('shows the empty picker and permits a solo preview', async () => {
  empty = true; open();
  expect(await screen.findByText(label('emptyPicker'))).toBeInTheDocument();
  expect(screen.getByRole('button', { name: label('previewAlone') })).toBeEnabled();
});
it('reports a failed request and permits a retry', async () => {
  fail = true; open();
  fireEvent.click(await screen.findByRole('button', { name: label('previewAlone') }));
  expect(await screen.findByRole('alert')).toHaveTextContent(label('failed'));
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: label('previewAlone') }));
  await waitFor(() => expect(screen.getByTestId('preview')).toBeInTheDocument());
  expect(requests).toHaveLength(2);
});

it('invalidates saved details and previews when purchase state changes', async () => {
  const { act, renderHook } = await import('@testing-library/react');
  const { useConsidering } = await import('@/lib/hooks/use-considering');
  const { queryKeys } = await import('@/lib/hooks/query-keys');
  client.setQueryData(queryKeys.outfit('saved'), { unowned_item_ids: ['anchor'] });
  client.setQueryData(queryKeys.outfitPreview('saved'), { image_url: '/preview.jpg' });
  vi.mocked(fetch).mockImplementation(async (_url, options) => new Response(JSON.stringify(options?.method === 'POST' ? { state: 'bought', item: { id: 'anchor' } } : [])));
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const { result } = renderHook(() => useConsidering('considering'), { wrapper });
  await act(() => result.current.change.mutateAsync({ id: 'anchor', action: 'bought' }));
  expect(client.getQueryState(queryKeys.outfit('saved'))?.isInvalidated).toBe(true);
  expect(client.getQueryState(queryKeys.outfitPreview('saved'))?.isInvalidated).toBe(true);
});
