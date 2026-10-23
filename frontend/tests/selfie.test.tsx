import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createQueryClient } from '@/lib/query-client';
import { toast } from 'sonner';
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }));
import SelfiePage from '@/app/dashboard/selfie/page';

const fetchMock = vi.mocked(fetch);
let revoke: ReturnType<typeof vi.fn>;
function mount() {
  const client = createQueryClient();
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
  vi.mocked(toast.error).mockClear();
  revoke = vi.fn();
  URL.createObjectURL = vi.fn(() => 'blob:selfie');
  URL.revokeObjectURL = revoke;
});
afterEach(cleanup);

describe('selfie analysis', () => {
  it('uploads once, shows matches and exposes no logging action', async () => {
    fetchMock.mockResolvedValue(reply({ matches: [{ id: 'shirt', name: 'Blue shirt' }], considered: 1 }));
    mount();
    expect(fetchMock).not.toHaveBeenCalled();
    const file = choose();
    expect(await screen.findByText('Blue shirt')).toBeInTheDocument();
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/selfie$/);
    expect(options?.method).toBe('POST');
    expect((options?.body as FormData).get('image')).toBe(file);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.queryAllByRole('button')).toHaveLength(0);
  });
  it('shows an empty result', async () => {
    fetchMock.mockResolvedValue(reply({ matches: [], considered: 0 }));
    mount(); choose();
    expect(await screen.findByText('noneFound')).toBeInTheDocument();
  });
  it.each([[503, 'aiOff'], [502, 'aiError'], [400, 'invalidPhoto'], [413, 'photoTooLarge'], [429, 'rateLimit']])('shows error %s', async (status, key) => {
    fetchMock.mockResolvedValue(reply({ detail: 'Unavailable' }, status as number));
    mount(); choose();
    expect(await screen.findByText(key as string)).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });
  it('disables replacement during analysis and clears old matches on a new photo', async () => {
    let resolve!: (response: Response) => void;
    fetchMock.mockResolvedValueOnce(reply({ matches: [{ id: 'shirt', name: 'Old shirt' }], considered: 1 }))
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
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByAltText('photoAlt')).not.toBeInTheDocument();
  });
});
