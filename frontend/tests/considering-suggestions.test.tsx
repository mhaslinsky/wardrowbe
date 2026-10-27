import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { ConsideringSuggestionsAction } from '@/components/considering-suggestions-action';
import { createQueryClient } from '@/lib/query-client';
import { sessionMock, translationMock } from './setup';

vi.mock('@/lib/hooks/use-user', () => ({ useUserToday: () => () => '2026-10-27' }));
vi.mock('@/lib/hooks/use-outfit-title', () => ({ useOutfitTitle: () => () => 'Look' }));
vi.mock('@/lib/hooks/use-translated-constants', () => ({ useTypeLabel: () => (value: string) => value, useOccasionLabel: () => (value: string) => value }));
const client = createQueryClient();
let status: number;
let empty: boolean;
const saved = { id: 'suggested', occasion: 'casual', source: 'on_demand', unowned_item_ids: ['anchor'], items: [{ id: 'anchor', type: 'shirt' }, { id: 'owned', type: 'pants' }] };
const label = (key: string) => `wardrobe.considering.${key}`;
beforeEach(() => {
  status = 201; empty = false;
  translationMock.includeNamespace = true;
  sessionMock.status = 'authenticated';
  sessionMock.data = { accessToken: 'suggest-token', expires: '2099-01-01', user: { email: 'suggest@example.test' } };
  vi.mocked(fetch).mockReset();
  vi.mocked(fetch).mockImplementation(async (url, options) => {
    if (options?.method === 'POST') return new Response(JSON.stringify(status === 201 ? empty ? [] : [saved] : { detail: status === 503 ? 'Internal AI is disabled' : 'Failed' }), { status });
    return new Response(JSON.stringify({ detail: 'No preview' }), { status: 404 });
  });
});
afterEach(() => { client.clear(); translationMock.includeNamespace = false; sessionMock.data = null; });
function open() {
  const view = render(<QueryClientProvider client={client}><ConsideringSuggestionsAction itemId="anchor" /></QueryClientProvider>);
  fireEvent.click(screen.getByRole('button', { name: label('wearWith') }));
  return view;
}
it('generates only on request and shows a marked normal card with preview action', async () => {
  open();
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: label('suggest') }));
  expect(await screen.findByText('outfits.cards.unbought')).toBeVisible();
  expect(screen.getByRole('button', { name: 'outfits.preview.see' })).toBeEnabled();
  const request = vi.mocked(fetch).mock.calls.find(([, options]) => options?.method === 'POST');
  expect(request?.[0]).toBe('/api/v1/considering/anchor/suggestions');
  expect(request?.[1]?.headers).toMatchObject({ Authorization: 'Bearer suggest-token' });
  expect(client.getQueryData(['outfit', 'suggested'])).toEqual(saved);
  fireEvent.click(screen.getByRole('button', { name: label('chooseAgain') }));
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(2));
});
it.each([400, 503])('shows request failure %i plainly', async failure => {
  status = failure; open();
  fireEvent.click(screen.getByRole('button', { name: label('suggest') }));
  expect(await screen.findByRole('alert')).toHaveTextContent(label(failure === 503 ? 'aiDisabled' : 'failed'));
  expect(screen.getByRole('button', { name: label('suggest') })).toHaveProperty('disabled', failure === 503);
});
it('rejects empty success and allows retry', async () => {
  empty = true; open();
  fireEvent.click(screen.getByRole('button', { name: label('suggest') }));
  expect(await screen.findByRole('alert')).toHaveTextContent(label('failed'));
  empty = false;
  fireEvent.click(screen.getByRole('button', { name: label('suggest') }));
  expect(await screen.findByText('outfits.cards.unbought')).toBeVisible();
});
it('aborts a pending request on cancel and reopens without stale results', async () => {
  let finish!: (response: Response) => void;
  let signal: AbortSignal | undefined;
  vi.mocked(fetch).mockImplementation((_url, options) => {
    signal = options?.signal as AbortSignal;
    return new Promise(resolve => { finish = resolve; });
  });
  open();
  fireEvent.click(screen.getByRole('button', { name: label('suggest') }));
  await waitFor(() => expect(signal).toBeDefined());
  expect(screen.getByRole('button', { name: label('suggest') })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'common.cancel' }));
  expect(signal?.aborted).toBe(true);
  finish(new Response(JSON.stringify([saved]), { status: 201 }));
  fireEvent.click(screen.getByRole('button', { name: label('wearWith') }));
  expect(screen.queryByText('outfits.cards.unbought')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: label('suggest') })).toBeEnabled();
  await waitFor(() => expect(client.getMutationCache().getAll()[0].state.status).toBe('success'));
  expect(client.getQueryData(['outfit', 'suggested'])).toBeUndefined();
});
