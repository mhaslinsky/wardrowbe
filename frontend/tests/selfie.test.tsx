import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryKeys } from '@/lib/hooks/query-keys';
import { createQueryClient } from '@/lib/query-client';
import { toast } from 'sonner';
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }));
import SelfiePage from '@/app/dashboard/selfie/page';

const fetchMock = vi.mocked(fetch);
const requestMock = vi.fn();
const wardrobe = [
  { id: 'shirt', name: 'Blue shirt', type: 't-shirt', status: 'ready' },
  { id: 'other-shirt', name: 'White shirt', type: 'shirt', status: 'ready' },
  { id: 'pants', name: 'Jeans', type: 'jeans', status: 'ready' },
];
const confirmBodies = () => fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/selfie/confirm-photo')).map(([, options]) => ({ request_id: (options?.body as FormData).get('request_id'), item_ids: JSON.parse(String((options?.body as FormData).get('item_ids'))) }));
let revoke: ReturnType<typeof vi.fn<(url: string) => void>>;
function mount(client = createQueryClient()) {
  return render(<QueryClientProvider client={client}><SelfiePage /></QueryClientProvider>);
}
function choose() {
  const file = new File(['photo'], 'me.jpg', { type: 'image/jpeg' });
  fireEvent.change(screen.getByLabelText('pick'), { target: { files: [file] } });
  return file;
}
function reply(body: unknown, status = 200) {
  return { ok: status === 200, status, json: async () => body } as Response;
}
beforeEach(() => {
  fetchMock.mockReset();
  requestMock.mockReset();
  fetchMock.mockImplementation((url, options) => String(url).includes('/items?')
    ? Promise.resolve(reply({ items: wardrobe, has_more: false }))
    : requestMock(url, options));
  vi.mocked(toast.error).mockClear();
  revoke = vi.fn();
  URL.createObjectURL = vi.fn(() => 'blob:selfie');
  URL.revokeObjectURL = revoke;
});
afterEach(cleanup);

describe('selfie analysis', () => {
  it('uploads once, shows matches and offers confirmation', async () => {
    requestMock.mockResolvedValue(reply({ matches: [{ id: 'shirt', name: 'Blue shirt' }], considered: 1 }));
    mount();
    expect(requestMock).not.toHaveBeenCalled();
    const file = choose();
    expect(await screen.findByText('Blue shirt')).toBeInTheDocument();
    const [url, options] = requestMock.mock.calls[0];
    expect(url).toMatch(/\/selfie$/);
    expect(options?.method).toBe('POST');
    expect((options?.body as FormData).get('image')).toBe(file);
    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'confirm' })).toBeEnabled();
  });
  it('shows an empty result', async () => {
    requestMock.mockResolvedValue(reply({ matches: [], considered: 0 }));
    mount(); choose();
    expect(await screen.findByText('noneFound')).toBeInTheDocument();
  });
  it.each([[503, 'aiOff'], [502, 'aiError'], [400, 'invalidPhoto'], [413, 'photoTooLarge'], [429, 'rateLimit']])('shows error %s', async (status, key) => {
    requestMock.mockResolvedValue(reply({ detail: 'Unavailable' }, status as number));
    mount(); choose();
    expect(await screen.findByText(key as string)).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });
  it('disables replacement during analysis and clears old matches on a new photo', async () => {
    let resolve!: (response: Response) => void;
    requestMock.mockResolvedValueOnce(reply({ matches: [{ id: 'shirt', name: 'Old shirt' }], considered: 1 }))
      .mockImplementationOnce(() => new Promise<Response>((done) => { resolve = done; }));
    const view = mount(); choose();
    await screen.findByText('Old shirt');
    choose();
    await waitFor(() => expect(screen.getByLabelText('pick')).toBeDisabled());
    expect(screen.queryByText('Old shirt')).not.toBeInTheDocument();
    resolve(reply({ matches: [], considered: 1 }));
    await screen.findByText('noneFound');
    expect(screen.getByLabelText('pick')).toBeEnabled();
    view.unmount();
    expect(revoke).toHaveBeenCalledWith('blob:selfie');
  });
  it('cancelling selection clears the preview without uploading', async () => {
    mount();
    fireEvent.change(screen.getByLabelText('pick'), { target: { files: [] } });
    expect(requestMock).not.toHaveBeenCalled();
    expect(screen.queryByAltText('photoAlt')).not.toBeInTheDocument();
  });
});


describe('selfie confirmation', () => {
  it('swaps within a slot, adds and removes items, and logs corrected items', async () => {
    requestMock.mockResolvedValueOnce(reply({ matches: [wardrobe[0]], considered: 3 }))
      .mockResolvedValueOnce(reply({ id: 'logged-outfit' }));
    const client = createQueryClient();
    const caches = [queryKeys.outfits.all, queryKeys.calendarOutfits.all, queryKeys.pendingOutfits.all, queryKeys.analytics.all, queryKeys.learning.all, queryKeys.everyItem, queryKeys.wearHistory.all, queryKeys.wearStats.all];
    for (const key of caches) client.setQueryData(key, { sentinel: true });
    mount(client); choose();
    await screen.findByText('Blue shirt');
    fireEvent.click(screen.getByRole('button', { name: 'swap' }));
    expect(screen.queryByRole('button', { name: 'Jeans' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'White shirt' }));
    expect(screen.queryByText('Blue shirt')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'add' }));
    fireEvent.click(screen.getByRole('button', { name: 'Jeans' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'remove' })[0]);
    fireEvent.click(screen.getByRole('button', { name: 'confirm' }));
    await screen.findByText('logged');
    for (const key of caches) expect(client.getQueryState(key)?.isInvalidated).toBe(true);
    expect(confirmBodies()[0].item_ids).toEqual(['pants']);
    expect(screen.getByRole('link', { name: 'viewOutfit' })).toHaveAttribute('href', '/dashboard/outfits/logged-outfit');
    expect(screen.getByRole('button', { name: 'confirm' })).toBeDisabled();
  });
  it('keeps request ids for retries and changes them after a correction or photo reset', async () => {
    requestMock.mockResolvedValueOnce(reply({ matches: [wardrobe[0]], considered: 3 }))
      .mockResolvedValue(reply({ detail: 'Unavailable' }, 502));
    mount(); choose();
    await screen.findByText('Blue shirt');
    for (let attempt = 0; attempt < 2; attempt++) {
      fireEvent.click(screen.getByRole('button', { name: 'confirm' }));
      await screen.findByText('confirmError');
    }
    expect(confirmBodies()).toHaveLength(2);
    expect(confirmBodies()[0].request_id).toBe(confirmBodies()[1].request_id);
    fireEvent.click(screen.getByRole('button', { name: 'remove' }));
    expect(screen.getByRole('button', { name: 'confirm' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'add' }));
    fireEvent.click(screen.getByRole('button', { name: 'Jeans' }));
    fireEvent.click(screen.getByRole('button', { name: 'confirm' }));
    await screen.findByText('confirmError');
    expect(confirmBodies()[2].request_id).not.toBe(confirmBodies()[0].request_id);
    requestMock.mockResolvedValueOnce(reply({ matches: [], considered: 3 }));
    choose();
    await screen.findByText('noneFound');
    expect(screen.queryByText('Jeans')).not.toBeInTheDocument();
    expect(screen.queryByText('confirmError')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'confirm' })).toBeDisabled();
  });
  it.each([['DAY_LOGGED', 'dayLogged', 409], ['REQUEST_REUSED', 'requestReused', 409], ['CONFIRM_RESULT_DELETED', 'resultDeleted', 410]])('shows %s', async (code, label, status) => {
    requestMock.mockResolvedValueOnce(reply({ matches: [wardrobe[0]], considered: 3 }))
      .mockResolvedValueOnce(reply({ detail: { error_code: code, existing_ids: ['existing'] } }, status as number));
    mount(); choose();
    await screen.findByText('Blue shirt');
    fireEvent.click(screen.getByRole('button', { name: 'confirm' }));
    await screen.findByText(label as string);
    if (code === 'DAY_LOGGED') expect(screen.getByRole('link', { name: 'viewOutfit' })).toHaveAttribute('href', '/dashboard/outfits/existing');
    expect(toast.error).not.toHaveBeenCalled();
  });
});

it('keeps the selected file for photo confirmation', async () => {
  requestMock.mockResolvedValueOnce(reply({ matches: [{ id: 'shirt', name: 'Blue shirt' }], considered: 1 }))
    .mockResolvedValueOnce(reply({ id: 'logged' }));
  mount(); const file = choose();
  await screen.findByText('Blue shirt');
  fireEvent.click(screen.getByRole('button', { name: 'confirm' }));
  await screen.findByText('logged');
  const call = requestMock.mock.calls.find(([url]) => String(url).endsWith('/selfie/confirm-photo'));
  expect(call).toBeDefined();
  expect((call![1].body as FormData).get('image')).toBe(file);
});
