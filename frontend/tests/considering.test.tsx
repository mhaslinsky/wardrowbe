import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ConsideringPage from '@/app/dashboard/considering/page';
import { createQueryClient } from '@/lib/query-client';
import { sessionMock, translationMock } from './setup';

const entry = {
  state: 'considering', passed_reason: null as string | null, source_url: 'https://shop.example/shirt',
  price: '29.50', currency: 'USD', planned_size: 'XL',
  item: { id: 'shirt1', name: 'Linen shirt', brand: 'Acme', image_url: '/shirt.jpg' },
};
let saved: typeof entry[];
let client: ReturnType<typeof createQueryClient>;
let conflict: boolean;
beforeEach(() => {
  translationMock.includeNamespace = true;
  sessionMock.status = 'authenticated';
  sessionMock.data = { accessToken: 'shelf-token', expires: '2099-01-01', user: { email: 'shelf@example.test' } };
  saved = [structuredClone(entry)];
  conflict = false;
  client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false } });
  vi.stubGlobal('confirm', vi.fn(() => true));
  vi.mocked(fetch).mockReset();
  vi.mocked(fetch).mockImplementation(async (url, options) => {
    const address = String(url);
    const current = saved[0];
    if (options?.method === 'POST' || options?.method === 'PATCH') {
      if (conflict) {
        current.state = 'bought';
        return new Response(JSON.stringify({ detail: 'Item already bought' }), { status: 409 });
      }
      const body = options.body ? JSON.parse(String(options.body)) : {};
      if (options.method === 'PATCH') {
        current.item.name = body.name;
        Object.assign(current, { price: body.price, currency: body.currency, planned_size: body.planned_size });
      } else {
        current.state = address.endsWith('/bought') ? 'bought' : address.endsWith('/passed') ? 'passed' : 'considering';
        current.passed_reason = body.reason ?? null;
      }
      return new Response(JSON.stringify(current));
    }
    const state = new URL(address, 'http://test').searchParams.get('state');
    return new Response(JSON.stringify(saved.filter((value) => value.state === state)));
  });
});
afterEach(() => {
  client.clear();
  translationMock.includeNamespace = false;
  sessionMock.data = null;
  sessionMock.status = 'unauthenticated';
  vi.unstubAllGlobals();
});
function openPage() { render(<QueryClientProvider client={client}><ConsideringPage /></QueryClientProvider>); }
const label = (key: string) => `wardrobe.considering.${key}`;
const button = (key: string) => screen.getByRole('button', { name: ['edit', 'cancel'].includes(key) ? `common.${key}` : label(key), exact: true });
function submitDialog() { fireEvent.submit(within(screen.getByRole('dialog')).getByRole('button', { name: 'common.save' }).closest('form')!); }
function writes() { return vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method !== 'GET'); }

it('lists the photo, name, brand, safe source link, currency price and planned size', async () => {
  openPage();
  expect(await screen.findByRole('heading', { name: 'Linen shirt' })).toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'Linen shirt' })).toHaveAttribute('src', '/shirt.jpg');
  expect(screen.getByText('Acme')).toBeInTheDocument();
  expect(screen.getByText(/USD.*29.50/)).toBeInTheDocument();
  expect(screen.getByText('XL')).toBeInTheDocument();
  const source = screen.getByRole('link', { name: label('source') });
  expect(source).toHaveAttribute('href', entry.source_url);
  expect(source).toHaveAttribute('target', '_blank');
  expect(source).toHaveAttribute('rel', 'noopener noreferrer');
});
it('shows distinct empty states for both tabs', async () => {
  saved = []; openPage();
  expect(await screen.findByText(label('empty'))).toBeInTheDocument();
  fireEvent.click(button('passed'));
  expect(await screen.findByText(label('emptyPassed'))).toBeInTheDocument();
});
it('confirms Bought it, removes it from the shelf and links to the wardrobe', async () => {
  openPage(); await screen.findByText('Linen shirt');
  vi.mocked(confirm).mockReturnValueOnce(false);
  fireEvent.click(button('bought'));
  expect(writes()).toHaveLength(0);
  fireEvent.click(button('bought'));
  expect(confirm).toHaveBeenCalledWith(label('buyConfirm'));
  expect(await screen.findByText(label('empty'))).toBeInTheDocument();
  expect(screen.getByRole('link', { name: label('inWardrobe') })).toHaveAttribute('href', '/dashboard/wardrobe?item=shirt1');
});
it('requires a typed reason, passes the item and shows its reason on Passed', async () => {
  openPage(); await screen.findByText('Linen shirt'); fireEvent.click(button('pass'));
  const reason = screen.getByLabelText(label('reason'));
  expect(reason).toBeRequired();
  fireEvent.change(reason, { target: { value: '   ' } }); submitDialog();
  expect(await screen.findByRole('alert')).toHaveTextContent(label('reasonRequired'));
  expect(writes()).toHaveLength(0);
  fireEvent.change(reason, { target: { value: ' Too short ' } }); submitDialog();
  await screen.findByText(label('empty'));
  fireEvent.click(button('passed'));
  expect(await screen.findByText('Too short')).toBeInTheDocument();
});
it('moves a passed item back to Considering', async () => {
  saved[0].state = 'passed'; saved[0].passed_reason = 'Too short';
  openPage(); await screen.findByText(label('empty')); fireEvent.click(button('passed'));
  await screen.findByText('Too short'); fireEvent.click(button('restore'));
  await screen.findByText(label('emptyPassed')); fireEvent.click(button('title'));
  expect(await screen.findByText('Linen shirt')).toBeInTheDocument();
  expect(screen.queryByText('Too short')).not.toBeInTheDocument();
});
it('edits name and purchase fields and resets the dialog after cancel', async () => {
  openPage(); await screen.findByText('Linen shirt'); fireEvent.click(button('edit'));
  fireEvent.change(screen.getByLabelText(label('name')), { target: { value: 'Discard me' } });
  fireEvent.click(button('cancel')); fireEvent.click(button('edit'));
  expect(screen.getByLabelText(label('name'))).toHaveValue('Linen shirt');
  for (const [key, value] of [['name', 'Blue shirt'], ['price', '0'], ['currency', 'EUR'], ['size', 'L']]) {
    fireEvent.change(screen.getByLabelText(label(key)), { target: { value } });
  }
  submitDialog();
  expect(await screen.findByRole('heading', { name: 'Blue shirt' })).toBeInTheDocument();
  expect(screen.getByText(/EUR.*0.00/)).toBeInTheDocument();
  expect(screen.getByText('L')).toBeInTheDocument();
});
it('shows a 409 error and refetches the shelf', async () => {
  openPage(); await screen.findByText('Linen shirt'); conflict = true;
  fireEvent.click(button('bought'));
  expect(await screen.findByRole('alert')).toHaveTextContent(label('conflict'));
  await screen.findByText(label('empty'));
  expect(screen.queryByText('Linen shirt')).not.toBeInTheDocument();
});
it('handles missing purchase fields and clears optional edit fields', async () => {
  Object.assign(saved[0], { source_url: null, price: null, planned_size: null });
  openPage(); await screen.findByText('Linen shirt');
  expect(screen.queryByRole('link', { name: label('source') })).not.toBeInTheDocument();
  fireEvent.click(button('edit')); submitDialog();
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(JSON.parse(String(writes()[0][1]?.body))).toMatchObject({ price: null, planned_size: null });
});
it('rejects malformed purchase fields before writing', async () => {
  openPage(); await screen.findByText('Linen shirt'); fireEvent.click(button('edit'));
  fireEvent.change(screen.getByLabelText(label('currency')), { target: { value: 'XX' } }); submitDialog();
  expect(await screen.findByRole('alert')).toHaveTextContent(label('invalid'));
  expect(writes()).toHaveLength(0);
});
it('shows a load error separately from an empty shelf', async () => {
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: 'Unavailable' }), { status: 503 }));
  openPage(); expect(await screen.findByRole('alert')).toHaveTextContent(label('loadFailed'));
  expect(screen.queryByText(label('empty'))).not.toBeInTheDocument();
});

it.each(['bought', 'passed', 'restore'])('applies successful %s before a failed refetch', async (action) => {
  if (action === 'restore') saved[0].state = 'passed';
  openPage();
  if (action === 'restore') {
    await screen.findByText(label('empty'));
    fireEvent.click(button('passed'));
  }
  await screen.findByText('Linen shirt');
  const realFetch = vi.mocked(fetch).getMockImplementation()!;
  let written = false;
  vi.mocked(fetch).mockImplementation(async (...args) => {
    if (written && args[1]?.method === 'GET') return new Response(JSON.stringify({ detail: 'Unavailable' }), { status: 503 });
    const response = await realFetch(...args);
    if (args[1]?.method === 'POST') written = true;
    return response;
  });
  if (action === 'passed') {
    fireEvent.click(button('pass'));
    fireEvent.change(screen.getByLabelText(label('reason')), { target: { value: 'Too short' } });
    submitDialog();
  } else fireEvent.click(button(action === 'bought' ? 'bought' : 'restore'));
  await waitFor(() => expect(client.getQueryState(['considering', 'shelf@example.test', action === 'restore' ? 'passed' : 'considering'])?.status).toBe('error'));
  expect(screen.queryByRole('heading', { name: 'Linen shirt' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: label('bought') })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: label('restore') })).not.toBeInTheDocument();
});
it('restores focus to Edit after cancelling its dialog', async () => {
  openPage(); await screen.findByText('Linen shirt');
  const edit = button('edit'); edit.focus(); fireEvent.click(edit);
  fireEvent.click(button('cancel'));
  await waitFor(() => expect(edit).toHaveFocus());
});
it('focuses the page heading when passing removes the dialog opener', async () => {
  openPage(); await screen.findByText('Linen shirt');
  button('pass').focus(); fireEvent.click(button('pass'));
  fireEvent.change(screen.getByLabelText(label('reason')), { target: { value: 'Too short' } });
  submitDialog();
  await waitFor(() => expect(screen.getByRole('heading', { level: 1 })).toHaveFocus());
});
it('invalidates wardrobe item and outfit caches after buying', async () => {
  const keys = [['items'], ['item', 'shirt1'], ['outfits'], ['calendarOutfits'], ['pendingOutfits'], ['analytics']];
  for (const key of keys) client.setQueryData(key, []);
  openPage(); await screen.findByText('Linen shirt'); fireEvent.click(button('bought'));
  await screen.findByRole('link', { name: label('inWardrobe') });
  for (const key of keys) expect(client.getQueryState(key)?.isInvalidated, key.join('/')).toBe(true);
});
