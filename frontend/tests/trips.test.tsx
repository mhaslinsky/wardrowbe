import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import * as intl from 'next-intl';
import tripMessages from '@/messages/en/trips.json';
import commonMessages from '@/messages/en/common.json';
import constantMessages from '@/messages/en/constants.json';
import TripsPage from '@/app/dashboard/trips/page';
import { useTrips, type Trip } from '@/lib/hooks/use-trips';
import { createQueryClient } from '@/lib/query-client';
import { sessionMock, translationMock } from './setup';

const trip: Trip = {
  id: 'trip1', destination: 'Paris', start_date: '2026-11-01', end_date: '2026-11-04', occasions: ['casual'],
  days: [1, 2, 3, 4].map((day) => ({
    date: `2026-11-0${day}`, outfit_id: `outfit${day}`,
    weather_source: day === 4 ? 'climate' : 'forecast', low: 10, high: 18,
  })),
  packing: [{ item_id: 'shoes', name: 'Boots', count: 1, days_worn: 4 },
    { item_id: 'shirt', name: null, count: 1, days_worn: 1 }],
};
let client: ReturnType<typeof createQueryClient>;
let saved: Trip[];
let failure: { status: number; detail: unknown } | undefined;
beforeEach(() => {
  translationMock.includeNamespace = true;
  sessionMock.status = 'authenticated';
  sessionMock.data = { accessToken: 'trip-token', expires: '2099-01-01', user: { email: 'trip@example.test' } };
  client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false } });
  saved = [];
  failure = undefined;
  vi.stubGlobal('confirm', vi.fn(() => true));
  vi.mocked(fetch).mockReset();
  vi.mocked(fetch).mockImplementation(async (_url, options) => {
    if (failure) return new Response(JSON.stringify({ detail: failure.detail }), { status: failure.status });
    if (options?.method === 'POST') {
      saved = [trip];
      return new Response(JSON.stringify(trip), { status: 201 });
    }
    if (options?.method === 'DELETE') {
      saved = [];
      return new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify(saved));
  });
});
afterEach(() => {
  client.clear();
  translationMock.includeNamespace = false;
  sessionMock.data = null;
  sessionMock.status = 'unauthenticated';
  vi.unstubAllGlobals();
});
function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
async function openPage() {
  render(<TripsPage />, { wrapper });
  await waitFor(() => expect(client.getQueryState(['trips', 'trip@example.test'])?.status).toBe('success'));
}
function fill() {
  for (const [label, value] of [['destination', ' Paris '], ['departure', '2026-11-01'], ['return', '2026-11-04']]) {
    fireEvent.change(screen.getByLabelText(`trips.${label}`), { target: { value } });
  }
}
function submit() { fireEvent.submit(screen.getByRole('button', { name: 'trips.plan' }).closest('form')!); }
function writes(method: string) { return vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === method); }

it('plans four days, links each outfit, lists each packing item once, and resets the form', async () => {
  await openPage();
  expect(screen.getByText('trips.empty')).toBeVisible();
  fill(); submit();
  expect(await screen.findByRole('heading', { name: 'Paris' })).toBeVisible();
  expect(writes('POST')[0][0]).toBe('/api/v1/trips');
  expect(JSON.parse(writes('POST')[0][1]!.body as string)).toEqual({
    destination: 'Paris', start_date: '2026-11-01', end_date: '2026-11-04', occasions: ['casual'],
  });
  for (const day of trip.days) expect(screen.getByRole('link', { name: day.date })).toHaveAttribute('href', `/dashboard/outfits/${day.outfit_id}`);
  expect(screen.getAllByRole('link', { name: 'Boots' })).toHaveLength(1);
  expect(screen.getByRole('link', { name: 'trips.unnamed' })).toHaveAttribute('href', '/dashboard/wardrobe?item=shirt');
  expect(screen.getAllByText(/trips.quantity/)).toHaveLength(2);
  expect(screen.getByText(/trips.climate/)).toBeVisible();
  expect(screen.getAllByText(/trips.forecast/)).toHaveLength(3);
  expect(screen.getByLabelText('trips.destination')).toHaveValue('');
});

it('loads saved trips and cancels or confirms deletion', async () => {
  saved = [trip];
  await openPage();
  vi.mocked(confirm).mockReturnValueOnce(false);
  fireEvent.click(screen.getByRole('button', { name: 'common.delete' }));
  expect(writes('DELETE')).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'common.delete' }));
  expect(confirm).toHaveBeenCalledWith('trips.deleteConfirm');
  await screen.findByText('trips.empty');
  expect(writes('DELETE')[0][0]).toBe('/api/v1/trips/trip1');
  expect(screen.queryByRole('heading', { name: 'Paris' })).not.toBeInTheDocument();
});

it.each([
  [409, 'Trip dates overlap', 'overlap'], [422, [{ msg: 'Invalid date' }], 'validation'],
  [503, 'Trip weather unavailable', 'weatherUnavailable'], [503, 'AI planning is disabled', 'aiDisabled'],
  [503, 'Trip planning unavailable', 'unavailable'], [429, 'Limit', 'rateLimit'],
])('shows a translated %s planning error and clears it on editing', async (status, detail, key) => {
  await openPage(); fill();
  failure = { status: status as number, detail };
  submit();
  expect(await screen.findByRole('alert')).toHaveTextContent(`trips.${key}`);
  fireEvent.change(screen.getByLabelText('trips.destination'), { target: { value: 'London' } });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it.each(['', ' ', 'missing-date', 'too-long', 'reversed'])('rejects invalid input (%s) without calling plan', async (kind) => {
  await openPage(); fill();
  if (kind === '' || kind === ' ') fireEvent.change(screen.getByLabelText('trips.destination'), { target: { value: kind } });
  else fireEvent.change(screen.getByLabelText('trips.return'), {
    target: { value: kind === 'missing-date' ? '' : kind === 'too-long' ? '2026-11-15' : '2026-10-31' },
  });
  submit();
  expect(screen.getByRole('alert')).toHaveTextContent('trips.validation');
  expect(writes('POST')).toHaveLength(0);
});

it('retains a trip after failed deletion and allows retry', async () => {
  saved = [trip]; await openPage();
  failure = { status: 503, detail: 'Trip planning unavailable' };
  fireEvent.click(screen.getByRole('button', { name: 'common.delete' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('trips.deleteFailed');
  expect(screen.getByRole('heading', { name: 'Paris' })).toBeVisible();
  failure = undefined;
  fireEvent.click(screen.getByRole('button', { name: 'common.delete' }));
  await screen.findByText('trips.empty');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('shows a list error rather than an empty result', async () => {
  failure = { status: 422, detail: 'Invalid request' };
  render(<TripsPage />, { wrapper });
  expect(await screen.findByRole('alert')).toHaveTextContent('trips.loadFailed');
  expect(screen.queryByText('trips.empty')).not.toBeInTheDocument();
});

it('waits for session resolution and sends its token', async () => {
  sessionMock.status = 'loading';
  const { result, rerender } = renderHook(() => useTrips(), { wrapper });
  expect(result.current.trips.fetchStatus).toBe('idle');
  expect(fetch).not.toHaveBeenCalled();
  sessionMock.status = 'authenticated'; rerender();
  await waitFor(() => expect(result.current.trips.isSuccess).toBe(true));
  expect(fetch).toHaveBeenCalledWith('/api/v1/trips', expect.objectContaining({
    headers: expect.objectContaining({ Authorization: 'Bearer trip-token' }),
  }));
});

it('refreshes trips, calendar and outfit caches after both writes', async () => {
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const { result } = renderHook(() => useTrips(), { wrapper });
  await waitFor(() => expect(result.current.trips.isSuccess).toBe(true));
  await act(async () => { await result.current.plan.mutateAsync(trip); });
  await act(async () => { await result.current.remove.mutateAsync(trip.id); });
  for (const prefix of ['trips', 'calendarOutfits', 'outfits', 'pendingOutfits']) {
    expect(invalidate.mock.calls.filter(([filter]) => filter?.queryKey?.[0] === prefix)).toHaveLength(2);
  }
});

it.each([1, 14])('accepts a valid %i-day trip', async (days) => {
  await openPage(); fill();
  const end = '2026-11-' + String(days).padStart(2, '0');
  fireEvent.change(screen.getByLabelText('trips.return'), { target: { value: end } });
  submit();
  await screen.findByRole('heading', { name: 'Paris' });
  expect(writes('POST')).toHaveLength(1);
  expect(writes('POST')[0][0]).toBe('/api/v1/trips');
  expect(JSON.parse(writes('POST')[0][1]!.body as string)).toMatchObject({ start_date: '2026-11-01', end_date: end });
});

it('rejects a 15-day trip before POST', async () => {
  await openPage(); fill();
  fireEvent.change(screen.getByLabelText('trips.return'), { target: { value: '2026-11-15' } });
  submit();
  expect(screen.getByRole('alert')).toHaveTextContent('trips.validation');
  expect(writes('POST')).toHaveLength(0);
});

it('renders singular and plural packing quantities and days', async () => {
  const realIntl = await vi.importActual<typeof intl>('next-intl');
  const messages = { trips: tripMessages, common: commonMessages, constants: constantMessages };
  const translation = vi.spyOn(intl, 'useTranslations').mockImplementation(((namespace: 'trips' | 'common' | 'constants') =>
    realIntl.createTranslator({ locale: 'en', messages, namespace })) as typeof intl.useTranslations);
  try {
    saved = [{ ...trip, packing: [
      { item_id: 'one', name: 'Shirt', count: 1, days_worn: 1 },
      { item_id: 'two', name: 'Boots', count: 2, days_worn: 2 },
    ] }];
    await openPage();
    expect(screen.getByText(/Pack 1 item; worn 1 day/)).toBeVisible();
    expect(screen.getByText(/Pack 2 items; worn 2 days/)).toBeVisible();
  } finally { translation.mockRestore(); }
});
