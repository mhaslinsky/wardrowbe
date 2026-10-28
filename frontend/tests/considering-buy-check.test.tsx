import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ConsideringPage from '@/app/dashboard/considering/page';
import { createQueryClient } from '@/lib/query-client';
import { sessionMock, translationMock } from './setup';

vi.mock('next-intl', () => ({
  useTranslations: (namespace: string) => (name: string, values?: { item?: string }) =>
    name === 'checkItem' ? `Check: ${values?.item}` : `${namespace}.${name}`,
}));

const original = { state: 'considering', source_url: null, passed_reason: null, price: null, currency: 'USD', planned_size: 'M', item: { id: 'candidate', name: 'Linen shirt', status: 'ready', brand: 'Acme' } };
let entry = structuredClone(original);
let result: Record<string, unknown>;
let client: ReturnType<typeof createQueryClient>;
let finish: (() => void) | undefined;
let hold: boolean;
let networkError: boolean;
const key = (name: string) => `wardrobe.considering.buyCheck.${name}`;
const checks = () => vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith('/buy-check'));
const citation = { source_id: 'note', item_id: 'owned-shirt', field: 'notes.fit', quote: '<b>collar stays flat</b>' };
const reason = { kind: 'fit_and_cut', citations: [citation] };
beforeEach(() => {
  translationMock.includeNamespace = true;
  sessionMock.status = 'authenticated';
  sessionMock.data = { accessToken: 'test', expires: '2099-01-01', user: { email: 'test@example.test' } };
  entry = structuredClone(original);
  result = { status: 'scored', percentage: 72, pluses: [reason], minuses: [], unknowns: [], outfits: { status: 'known', count: 1, ideas: [{ item_ids: ['candidate', 'owned-shirt'] }] }, near_duplicate: false, model_used: 'hidden-model', requested_model: 'hidden-request', fallback_reason: 'hidden-fallback' };
  hold = false; networkError = false; finish = undefined;
  client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false }, mutations: { retry: false } });
  vi.mocked(fetch).mockReset();
  vi.mocked(fetch).mockImplementation(async (url, options) => {
    const address = String(url);
    if (address.includes('/items/')) return new Response(JSON.stringify({ id: 'owned-shirt', name: 'Buck Mason tee' }));
    if (address.endsWith('/buy-check')) {
      if (hold) await new Promise<void>((resolve) => { finish = resolve; });
      return new Response(JSON.stringify(networkError ? { detail: 'Unavailable' } : result), { status: networkError ? 503 : 200 });
    }
    if (options?.method === 'PATCH') {
      const body = JSON.parse(String(options.body));
      entry = { ...entry, planned_size: body.planned_size, item: { ...entry.item, name: body.name } };
      return new Response(JSON.stringify(entry));
    }
    return new Response(JSON.stringify([entry]));
  });
});
afterEach(() => { client.clear(); translationMock.includeNamespace = false; sessionMock.data = null; sessionMock.status = 'unauthenticated'; });
async function open() {
  const view = render(<QueryClientProvider client={client}><ConsideringPage /></QueryClientProvider>);
  await screen.findByText('Linen shirt');
  return view;
}
async function check() { fireEvent.click(screen.getByRole('button', { name: 'Check: Linen shirt' })); }

it('makes no request before a click or on a shelf refetch; shows loading and blocks repeat clicks', async () => {
  hold = true; await open(); expect(checks()).toHaveLength(0);
  await client.refetchQueries(); expect(checks()).toHaveLength(0);
  await check();
  expect(await screen.findByText(key('loading'))).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Check: Linen shirt' })).toBeDisabled();
  expect(checks()).toHaveLength(1);
  expect(checks()[0][1]?.method).toBe('POST');
  finish?.(); await screen.findByText(key('scored'));
});
it('shows the score, cited dimension and outfits without rendering note HTML or model metadata', async () => {
  await open(); await check();
  expect(await screen.findByText(key('scored'))).toBeInTheDocument();
  expect(screen.getByText(key('fit_and_cut'))).toBeInTheDocument();
  expect(screen.getByText(/<b>collar stays flat<\/b>/)).toBeInTheDocument();
  expect(screen.getAllByRole('link', { name: 'Buck Mason tee' })[0]).toHaveAttribute('href', '/dashboard/wardrobe?item=owned-shirt');
  expect(screen.getByText(key('outfits'))).toBeInTheDocument();
  expect(document.querySelector('b')).toBeNull();
  expect(screen.queryByText(/hidden-/)).toBeNull();
});
it.each(['insufficient', 'skip', 'wrong_size', 'duplicate'])('shows %s instead of a percentage', async (status) => {
  result = { ...result, status, percentage: null, pluses: [], minuses: status === 'insufficient' ? [] : [{ kind: status, citations: [citation] }] };
  await open(); await check();
  expect(await screen.findByText(key(status), { selector: 'p' })).toBeInTheDocument();
  expect(screen.queryByText(key('scored'))).toBeNull();
});
it('shows a near duplicate warning above the percentage', async () => {
  result.near_duplicate = true; await open(); await check();
  const warning = await screen.findByText(key('nearDuplicate'));
  expect(warning.compareDocumentPosition(screen.getByText(key('scored'))) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
it.each(['missing_key', 'disabled', 'unavailable'])('shows the %s error beside Check', async (error) => {
  result = { ...result, status: 'error', error };
  await open(); await check();
  expect(await screen.findByRole('alert')).toHaveTextContent(key(error === 'unavailable' ? 'unavailable' : 'notConfigured'));
  expect(screen.queryByText(key('scored'))).toBeNull();
});
it('shows a retryable transport failure', async () => {
  networkError = true; await open(); await check();
  expect(await screen.findByRole('alert')).toHaveTextContent(key('unavailable'));
  networkError = false; await check(); await screen.findByText(key('scored'));
});
it.each(['name', 'size'])('clears the result after editing %s without another check', async (field) => {
  await open(); await check(); await screen.findByText(key('scored'));
  fireEvent.click(screen.getByRole('button', { name: 'common.edit' }));
  fireEvent.change(screen.getByLabelText(`wardrobe.considering.${field}`), { target: { value: field === 'size' ? 'L' : 'New shirt' } });
  fireEvent.click(screen.getByRole('button', { name: 'common.save' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await waitFor(() => expect(screen.queryByText(key('scored'))).toBeNull());
  expect(checks()).toHaveLength(1);
});
it('discards an in-flight result after an edit', async () => {
  hold = true; await open(); await check(); await screen.findByText(key('loading'));
  fireEvent.click(screen.getByRole('button', { name: 'common.edit' }));
  fireEvent.change(screen.getByLabelText('wardrobe.considering.size'), { target: { value: 'L' } });
  fireEvent.click(screen.getByRole('button', { name: 'common.save' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  finish?.();
  await waitFor(() => expect(checks()[0][1]?.signal?.aborted).toBe(true));
  expect(screen.queryByText(key('scored'))).toBeNull();
});

it.each([null, {}, { status: 'scored', percentage: 101 }, { status: 'mystery' }])('rejects a missing or malformed response %j', async (payload) => {
  result = payload as unknown as Record<string, unknown>;
  await open(); await check();
  expect(await screen.findByRole('alert')).toHaveTextContent(key('unavailable'));
});
it('distinguishes empty outfit options from unknown and not applicable', async () => {
  result.outfits = { status: 'known', count: 0, ideas: [] }; result.pluses = []; result.unknowns = [];
  await open(); await check(); await screen.findByText(key('outfitCount'));
  result.outfits = { status: 'unknown' }; await check(); await screen.findByText(key('outfitsUnknown'));
  result.outfits = { status: 'not_applicable' }; await check(); await screen.findByText(key('outfitsNotApplicable'));
});

it('sends one paid request for two clicks before a render and aborts it on unmount', async () => {
  hold = true;
  const view = await open();
  const button = screen.getByRole('button', { name: 'Check: Linen shirt' });
  act(() => { fireEvent.click(button); fireEvent.click(button); });
  await screen.findByText(key('loading'));
  const signals = checks().map(([, options]) => options!.signal!);
  view.unmount();
  expect(signals).toHaveLength(1);
  expect(signals.every(signal => signal.aborted)).toBe(true);
  finish?.();
});
it('renders anchor citations as plain item names and outfit entries as owned-item links', async () => {
  result.minuses = [{ kind: 'few_outfits', citations: [{ source_id: 'closet:outfits', item_id: 'candidate', field: 'closet.outfits_completed.count', value: 1 }] }];
  await open(); await check(); await screen.findByText(key('few_outfits'));
  expect(screen.getByText((_, element) => element?.tagName === 'SPAN' && element.textContent === 'Linen shirt: 1')).toBeInTheDocument();
  expect(document.querySelector('a[href="/dashboard/wardrobe?item=candidate"]')).toBeNull();
  expect(screen.getAllByRole('link', { name: 'Buck Mason tee' }).length).toBeGreaterThan(0);
});
it('lists three outfit ideas without the candidate until Show all is pressed', async () => {
  const ideas = Array.from({ length: 5 }, (_, index) => ({ item_ids: ['candidate', `owned-${index}`] }));
  result.outfits = { status: 'known', count: 5, ideas };
  await open(); await check();
  const list = await screen.findByRole('list', { name: key('outfits') });
  expect(within(list).getAllByRole('listitem')).toHaveLength(3);
  expect(within(list).queryByText('Linen shirt')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: key('showAllOutfits') }));
  expect(within(list).getAllByRole('listitem')).toHaveLength(5);
  fireEvent.click(screen.getByRole('button', { name: key('showFewerOutfits') }));
  expect(within(list).getAllByRole('listitem')).toHaveLength(3);
});
it('shows the sample note for a short sampled list and only after Show all for a long one', async () => {
  result.outfits = { status: 'known', count: 2, ideas: [{ item_ids: ['candidate', 'owned-0'] }, { item_ids: ['candidate', 'owned-1'] }], pool_truncated: true };
  await open(); await check();
  expect(await screen.findByText(key('sampled'))).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: key('showAllOutfits') })).toBeNull();
  result.outfits = { status: 'known', count: 5, ideas: Array.from({ length: 5 }, (_, index) => ({ item_ids: ['candidate', `owned-${index}`] })), pool_truncated: true };
  await check(); await screen.findByRole('button', { name: key('showAllOutfits') });
  expect(screen.queryByText(key('sampled'))).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: key('showAllOutfits') }));
  expect(screen.getByText(key('sampled'))).toBeInTheDocument();
});
it('names the Check button with its item while keeping the visible label short', async () => {
  await open();
  expect(screen.getByRole('button', { name: 'Check: Linen shirt' })).toHaveTextContent(key('check'));
});
