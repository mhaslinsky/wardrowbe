import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import InspirationPage from '@/app/dashboard/inspiration/page';
import { createQueryClient } from '@/lib/query-client';
import { translationMock } from './setup';
import savedLook from './fixtures/inspiration-look.json';

// Captured inspiration response with image query strings removed.
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }));
let client: ReturnType<typeof createQueryClient>;
let looks: typeof savedLook[];
let uploadStatus: number;
let deleteStatus: number;
beforeEach(() => {
  translationMock.includeNamespace = true;
  client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false } });
  looks = [];
  uploadStatus = 201;
  deleteStatus = 204;
  vi.mocked(toast.error).mockClear();
  vi.mocked(fetch).mockReset();
  vi.stubGlobal('confirm', vi.fn(() => true));
  vi.mocked(fetch).mockImplementation(async (_url, options) => {
    if (options?.method === 'POST') {
      if (uploadStatus !== 201) return new Response(JSON.stringify({ detail: 'Server detail' }), { status: uploadStatus });
      looks = [savedLook];
      return new Response(JSON.stringify(savedLook), { status: 201 });
    }
    if (options?.method === 'DELETE') {
      if (deleteStatus !== 204) return new Response(JSON.stringify({ detail: 'Delete failed' }), { status: deleteStatus });
      looks = [];
      return new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify(looks));
  });
});
afterEach(() => { client.clear(); translationMock.includeNamespace = false; vi.unstubAllGlobals(); });
async function openPage() {
  render(<QueryClientProvider client={client}><InspirationPage /></QueryClientProvider>);
  await waitFor(() => expect(client.getQueryState(['inspiration'])?.status).toBe('success'));
}
function choose(file = new File(['photo'], 'look.jpg', { type: 'image/jpeg' })) {
  fireEvent.change(screen.getByLabelText('inspiration.photo'), { target: { files: [file] } });
}
function submit() {
  fireEvent.submit(screen.getByRole('button', { name: 'inspiration.create' }).closest('form')!);
}
function uploads() { return vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === 'POST'); }

it('uploads once, renders saved slots through translated labels, and clears the selection', async () => {
  await openPage();
  expect(screen.getByRole('button', { name: 'inspiration.create' })).toBeDisabled();
  choose(); submit();
  expect(await screen.findByText('inspiration.saved')).toBeVisible();
  expect(screen.getByRole('button', { name: 'inspiration.create' })).toBeDisabled();
  expect(screen.getByLabelText('inspiration.photo')).toHaveValue('');
  expect(screen.getAllByText('inspiration.noMatch')).toHaveLength(savedLook.slots.filter((slot) => slot.no_match).length);
  const slot = savedLook.slots[0];
  expect(screen.getByText(`constants.colors.${slot.color}, constants.types.${slot.type}, constants.fits.${slot.fit}, constants.patterns.${slot.pattern}`)).toBeVisible();
  expect(screen.getByAltText('inspiration.photoAlt')).toHaveAttribute('src', savedLook.image_url);
  expect(screen.getByText(savedLook.items[0].name)).toBeVisible();
  submit();
  expect(uploads()).toHaveLength(1);
});

it.each([[413, 'photoTooLarge'], [415, 'invalidPhoto'], [422, 'checkPhotoAndLocation'], [429, 'rateLimit'], [503, 'unavailable']])('shows translated %s recovery without a server toast', async (status, key) => {
  uploadStatus = status as number;
  await openPage(); choose(); submit();
  expect(await screen.findByRole('alert')).toHaveTextContent(`inspiration.${key}`);
  expect(screen.getByRole('button', { name: 'inspiration.create' })).toBeEnabled();
  expect(screen.queryByText('inspiration.saved')).not.toBeInTheDocument();
  expect(toast.error).not.toHaveBeenCalled();
});

it.each([
  [new File(['text'], 'look.txt', { type: 'text/plain' }), 'invalidPhoto'],
  [new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'look.jpg', { type: 'image/jpeg' }), 'photoTooLarge'],
])('rejects invalid files before uploading', async (file, key) => {
  await openPage(); choose(file as File);
  expect(screen.getByRole('alert')).toHaveTextContent(`inspiration.${key}`);
  submit();
  expect(uploads()).toHaveLength(0);
  choose();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('confirms deletion and clears saved and delete error states before the next upload', async () => {
  await openPage(); choose(); submit();
  await screen.findByText('inspiration.saved');
  vi.mocked(confirm).mockReturnValueOnce(false);
  fireEvent.click(screen.getByRole('button', { name: 'common.delete' }));
  expect(vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === 'DELETE')).toHaveLength(0);
  deleteStatus = 503;
  fireEvent.click(screen.getByRole('button', { name: 'common.delete' }));
  expect(confirm).toHaveBeenLastCalledWith('inspiration.deleteConfirm');
  expect(await screen.findByRole('alert')).toHaveTextContent('inspiration.unavailable');
  expect(screen.queryByText('inspiration.saved')).not.toBeInTheDocument();
  expect(toast.error).not.toHaveBeenCalled();
  choose();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  submit();
  await screen.findByText('inspiration.saved');
  deleteStatus = 204;
  fireEvent.click(screen.getByRole('button', { name: 'common.delete' }));
  expect(await screen.findByText('inspiration.empty')).toBeVisible();
  expect(screen.queryByText('inspiration.saved')).not.toBeInTheDocument();
});

it('names added base layers', async () => {
  looks = [{ ...savedLook, items: [...savedLook.items, { id: 'base-layer', name: 'Cotton undershirt', image_path: 'base.jpg', image_url: '/base.jpg' }] }];
  await openPage();
  const row = screen.getByText('inspiration.baseLayer').parentElement!;
  expect(within(row).getByText('Cotton undershirt')).toBeVisible();
});

it('owns query error messages without an English toast', async () => {
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: 'Server detail' }), { status: 503 }));
  render(<QueryClientProvider client={client}><InspirationPage /></QueryClientProvider>);
  expect(await screen.findByRole('alert')).toHaveTextContent('inspiration.unavailable');
  expect(toast.error).not.toHaveBeenCalled();
});

it('keeps deletion disabled and blocks a second upload while a POST is pending', async () => {
  looks = [savedLook];
  let resolveUpload!: (response: Response) => void;
  const upload = new Promise<Response>((resolve) => { resolveUpload = resolve; });
  const respond = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((url, options) => options?.method === 'POST' ? upload : respond(url, options));
  await openPage(); choose(); submit();
  const working = await screen.findByRole('button', { name: 'inspiration.working' });
  const remove = screen.getByRole('button', { name: 'common.delete' });
  expect(remove).toBeDisabled();
  fireEvent.click(remove);
  fireEvent.submit(working.closest('form')!);
  expect(working).toBeDisabled();
  expect(uploads()).toHaveLength(1);
  expect(confirm).not.toHaveBeenCalled();
  await act(async () => { resolveUpload(new Response(JSON.stringify(savedLook), { status: 201 })); });
  expect(await screen.findByText('inspiration.saved')).toBeVisible();
  expect(screen.getByRole('button', { name: 'common.delete' })).toBeEnabled();
});

it('offers only endpoint-supported image formats in the picker', async () => {
  await openPage();
  expect(screen.getByLabelText('inspiration.photo')).toHaveAttribute('accept', '.jpg,.jpeg,.png,.webp');
  choose(new File(['photo'], 'look.heic', { type: 'image/heic' }));
  expect(screen.getByRole('alert')).toHaveTextContent('inspiration.invalidPhoto');
  submit();
  expect(uploads()).toHaveLength(0);
});

it('keeps the photo input disabled until an active deletion settles', async () => {
  looks = [savedLook];
  let resolveDelete!: (response: Response) => void;
  const deletion = new Promise<Response>((resolve) => { resolveDelete = resolve; });
  const respond = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((url, options) => options?.method === 'DELETE' ? deletion : respond(url, options));
  await openPage();
  fireEvent.click(screen.getByRole('button', { name: 'common.delete' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'common.delete' })).toBeDisabled());
  expect(screen.getByLabelText('inspiration.photo')).toBeDisabled();
  choose(); submit();
  expect(uploads()).toHaveLength(0);
  looks = [];
  await act(async () => { resolveDelete(new Response(null, { status: 204 })); });
  expect(await screen.findByText('inspiration.empty')).toBeVisible();
  expect(screen.getByLabelText('inspiration.photo')).toBeEnabled();
});
