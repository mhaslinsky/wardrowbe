/// <reference types="vite/client" />
import { act, fireEvent, render as testingRender, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ComponentType, ReactElement } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useOutfitPreview } from '@/lib/hooks/use-outfit-preview';

let client: QueryClient;
function render(ui: ReactElement) {
  return testingRender(ui, { wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> });
}

// Missing components render nothing, so these tests can also run against the base commit.
const modules = import.meta.glob('../components/{outfit-preview,preview-photo-settings}.tsx', { eager: true });
const OutfitPreview = (modules['../components/outfit-preview.tsx'] as { OutfitPreview?: ComponentType<{ outfitId: string }> } | undefined)?.OutfitPreview ?? (() => null);
const PreviewPhotoSettings = (modules['../components/preview-photo-settings.tsx'] as { PreviewPhotoSettings?: ComponentType } | undefined)?.PreviewPhotoSettings ?? (() => null);

const saved = { image_url: '/full.jpg', medium_url: '/medium.jpg', stale: false, created_at: '2026-10-24' };
const response = (data: unknown, status = 200) => ({ ok: status < 400, status, json: async () => data });
beforeEach(() => { vi.mocked(fetch).mockReset(); client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); });
afterEach(() => { client.clear(); vi.useRealTimers(); });

it('shows a saved preview without drawing, including on a second tap', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response(saved) as Response).mockResolvedValue(response({ ...saved, image_url: '/fresh', medium_url: '/fresh-medium' }) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(screen.getByText('see'));
  expect(await screen.findByRole('img')).toHaveAttribute('src', saved.medium_url);
  fireEvent.click(screen.getByText('see'));
  await waitFor(() => expect(screen.getByRole('img')).toHaveAttribute('src', '/fresh-medium'));
  expect(vi.mocked(fetch).mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true);
});

it('draws only after a missing saved preview and shows loading', async () => {
  let finish!: (value: Response) => void;
  vi.mocked(fetch).mockResolvedValueOnce(response({ detail: 'NO_PREVIEW' }, 404) as Response)
    .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(screen.getByText('see'));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(screen.getByRole('status')).toHaveTextContent('drawing');
  expect(screen.getByRole('button')).toBeDisabled();
  finish(response(saved) as Response);
  expect(await screen.findByRole('img')).toBeVisible();
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/outfits/one/preview', expect.objectContaining({ method: 'POST' }));
});

it('redraws stale previews and preserves the old image on failure', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response({ ...saved, stale: true }) as Response)
    .mockResolvedValueOnce(response({ detail: 'PREVIEW_FAILED' }, 502) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(screen.getByText('see'));
  fireEvent.click(await screen.findByText('redraw'));
  expect(await screen.findByRole('alert')).toHaveTextContent('failed');
  expect(screen.getByText('changed')).toBeVisible();
  expect(screen.getByRole('img')).toHaveAttribute('src', saved.medium_url);
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/outfits/one/preview?regenerate=true', expect.objectContaining({ method: 'POST' }));
});

it('links to Settings when the reference photo is missing', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response({ detail: 'NO_PREVIEW' }, 404) as Response)
    .mockResolvedValueOnce(response({ detail: { code: 'NO_PREVIEW_PHOTO' } }, 409) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(screen.getByText('see'));
  expect(await screen.findByText('addPhoto')).toHaveAttribute('href', '/dashboard/settings#preview-photo');
});

it('reports a failed saved-preview read without drawing', async () => {
  vi.mocked(fetch).mockResolvedValue(response({}, 503) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(screen.getByText('see'));
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('uploads, replaces, confirms removal and resets the file input', async () => {
  let photo: typeof saved | null = null;
  vi.mocked(fetch).mockImplementation(async (_url, options) => {
    if (options?.method === 'PUT') photo = saved;
    if (options?.method === 'DELETE') { photo = null; return response(null, 204) as Response; }
    return response(photo ?? { detail: 'NO_PREVIEW_PHOTO' }, photo ? 200 : 404) as Response;
  });
  render(<PreviewPhotoSettings />);
  const input = screen.getByLabelText('upload');
  await waitFor(() => expect(input).toBeEnabled());
  const file = new File(['photo'], 'photo.jpg', { type: 'image/jpeg' });
  fireEvent.change(input, { target: { files: [file] } });
  expect(await screen.findByRole('img')).toBeVisible();
  expect(input).toHaveValue('');
  expect((vi.mocked(fetch).mock.calls[1][1]?.body as FormData).get('image')).toEqual(file);
  fireEvent.change(screen.getByLabelText('replace'), { target: { files: [file] } });
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(2));
  await waitFor(() => expect(screen.getByText('remove')).toBeEnabled());
  fireEvent.click(screen.getByText('remove'));
  expect(screen.getByText('confirm')).toBeVisible();
  expect(vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === 'DELETE')).toHaveLength(0);
  fireEvent.click(screen.getByText('cancel'));
  expect(screen.queryByText('confirm')).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('remove'));
  fireEvent.click(screen.getByText('remove'));
  await waitFor(() => expect(screen.queryByRole('img')).not.toBeInTheDocument());
  expect(fetch).toHaveBeenCalledWith('/api/v1/users/me/preview-photo', expect.objectContaining({ method: 'DELETE' }));
  expect(client.getQueryState(['preview-photo'])?.isInvalidated).toBe(false);
});

it('reports photo load and upload failures', async () => {
  vi.mocked(fetch).mockResolvedValue(response({}, 502) as Response);
  render(<PreviewPhotoSettings />);
  expect(await screen.findByRole('alert')).toBeVisible();
  fireEvent.change(screen.getByLabelText('upload'), { target: { files: [new File(['bad'], 'bad.jpg')] } });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

it('rejects an empty preview response', async () => {
  vi.mocked(fetch).mockResolvedValue(response({}) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(screen.getByText('see'));
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

it('clears previews when the reference photo changes', async () => {
  vi.mocked(fetch).mockResolvedValue(response(saved) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(screen.getByText('see'));
  expect(await screen.findByRole('img')).toBeVisible();
  await act(async () => { await client.resetQueries({ queryKey: ['outfit-preview'] }); });
  await waitFor(() => expect(screen.queryByRole('img')).not.toBeInTheDocument());
});

it('ignores an earlier outfit request after navigation', async () => {
  let finish!: (value: Response) => void;
  vi.mocked(fetch).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const view = render(<OutfitPreview outfitId="one" />);
  fireEvent.click(screen.getByText('see'));
  await waitFor(() => expect(finish).toBeTypeOf('function'));
  view.rerender(<OutfitPreview outfitId="two" />);
  const json = vi.fn(async () => saved);
  await act(async () => { finish({ ...response(saved), json } as Response); });
  await waitFor(() => expect(json).toHaveBeenCalled());
  expect(screen.getByText('see')).toBeEnabled();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

it.each([{}, { detail: 'NOT_FOUND' }, { detail: { message: 'NO_PREVIEW' } }])('does not draw for unrelated 404: %j', async (data) => {
  vi.mocked(fetch).mockResolvedValue(response(data, 404) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(screen.getByText('see'));
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it.each(['NO_PREVIEW_PHOTO', { code: 'NO_PREVIEW_PHOTO' }, { error_code: 'NO_PREVIEW_PHOTO' }])('reads the exact missing-photo code: %j', async (detail) => {
  vi.mocked(fetch).mockResolvedValueOnce(response({ detail: 'NO_PREVIEW' }, 404) as Response)
    .mockResolvedValueOnce(response({ detail }, 409) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(screen.getByText('see'));
  expect(await screen.findByText('addPhoto')).toBeVisible();
});

it('ignores error-code text in unrelated fields', async () => {
  vi.mocked(fetch).mockResolvedValue(response({ detail: { message: 'NO_PREVIEW_PHOTO' } }, 409) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(screen.getByText('see'));
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(screen.queryByText('addPhoto')).not.toBeInTheDocument();
});

it('sends only one draw for two synchronous clicks', async () => {
  let finish!: (value: Response) => void;
  vi.mocked(fetch).mockResolvedValueOnce(response({ detail: 'NO_PREVIEW' }, 404) as Response)
    .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  render(<OutfitPreview outfitId="one" />);
  const button = screen.getByText('see');
  act(() => { button.click(); button.click(); });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  finish(response(saved) as Response);
  expect(await screen.findByRole('img')).toBeVisible();
  expect(vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
});

it.each([false, true])('polls an existing draw with timeout=%s', async (timeout) => {
  vi.useFakeTimers();
  vi.mocked(fetch).mockResolvedValueOnce(response({ detail: 'NO_PREVIEW' }, 404) as Response)
    .mockResolvedValueOnce(response({ detail: { error_code: 'PREVIEW_IN_PROGRESS' } }, 409) as Response)
    .mockResolvedValue(response(timeout ? { detail: 'NO_PREVIEW' } : saved, timeout ? 404 : 200) as Response);
  render(<OutfitPreview outfitId="one" />);
  await act(async () => { fireEvent.click(screen.getByText('see')); await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByRole('status')).toHaveTextContent('drawing');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(timeout ? 200_000 : 2000); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  if (timeout) expect(screen.getByRole('alert')).toBeVisible();
  else expect(screen.getByRole('img')).toHaveAttribute('src', saved.medium_url);
  expect(vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
});

it('keeps removal confirmation open when deletion fails', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response(saved) as Response).mockResolvedValue(response({}, 502) as Response);
  render(<PreviewPhotoSettings />);
  await screen.findByRole('img');
  fireEvent.click(screen.getByText('remove'));
  fireEvent.click(screen.getByText('remove'));
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(screen.getByText('confirm')).toBeVisible();
  expect(screen.getByText('cancel')).toBeEnabled();
});

it('stops polling on a read error', async () => {
  vi.useFakeTimers();
  vi.mocked(fetch).mockResolvedValueOnce(response({ detail: 'NO_PREVIEW' }, 404) as Response)
    .mockResolvedValueOnce(response({ detail: 'PREVIEW_IN_PROGRESS' }, 409) as Response)
    .mockResolvedValue(response({}, 503) as Response);
  render(<OutfitPreview outfitId="one" />);
  await act(async () => { fireEvent.click(screen.getByText('see')); await vi.advanceTimersByTimeAsync(0); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2001); });
  expect(screen.getByRole('alert')).toBeVisible();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(fetch).toHaveBeenCalledTimes(3);
});

it('invalidates preview and photo queries after replacing the photo', async () => {
  let replaced = false;
  vi.mocked(fetch).mockImplementation(async (_url, options) => {
    if (options?.method === 'PUT') replaced = true;
    return response({ ...saved, image_url: replaced ? '/new-photo' : saved.image_url }) as Response;
  });
  render(<><OutfitPreview outfitId="one" /><PreviewPhotoSettings /></>);
  fireEvent.click(screen.getByText('see'));
  expect(await screen.findByRole('img', { name: 'see' })).toBeVisible();
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  fireEvent.change(screen.getByLabelText('replace'), { target: { files: [new File(['photo'], 'photo.jpg')] } });
  await waitFor(() => expect(screen.getByRole('img', { name: 'title' }).closest('a')).toHaveAttribute('href', '/new-photo'));
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['preview-photo'] });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['outfit-preview'] });
  expect(screen.queryByRole('img', { name: 'see' })).not.toBeInTheDocument();
});

it('guards two draw calls before React renders', async () => {
  const completions: ((value: Response) => void)[] = [];
  vi.mocked(fetch).mockImplementation(async (_url, options) => options?.method === 'POST'
    ? new Promise((resolve) => { completions.push(resolve); })
    : response({ detail: 'NO_PREVIEW' }, 404) as Response);
  const { result } = renderHook(() => useOutfitPreview('one'), { wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> });
  let requests!: Promise<unknown>[];
  act(() => { requests = [result.current.draw(), result.current.draw()]; });
  await waitFor(() => expect(completions).toHaveLength(1));
  await act(async () => { completions.forEach((finish) => finish(response(saved) as Response)); await Promise.all(requests); });
  expect(vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
});
