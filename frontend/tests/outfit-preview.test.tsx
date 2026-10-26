/// <reference types="vite/client" />
import { act, fireEvent, render as testingRender, renderHook, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ComponentType, ReactElement } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useOutfitPreview } from '@/lib/hooks/use-outfit-preview';
import { createQueryClient } from '@/lib/query-client';
import { toast } from 'sonner';
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }));
const translation = vi.hoisted(() => ({ locale: null as 'ja' | 'zh-CN' | 'zh-TW' | null }));
vi.mock('next-intl', async (original) => {
  const actual = await original<typeof import('next-intl')>();
  const catalogs = {
    ja: (await import('@/messages/ja/settings.json')).default,
    'zh-CN': (await import('@/messages/zh-CN/settings.json')).default,
    'zh-TW': (await import('@/messages/zh-TW/settings.json')).default,
  };
  return { ...actual, useTranslations: (namespace?: string) => {
    if (!translation.locale) return (key: string) => key;
    return actual.createTranslator({
      locale: translation.locale, namespace,
      messages: { settings: catalogs[translation.locale], common: { remove: 'remove', cancel: 'cancel', loading: 'loading' } },
    });
  } };
});

function frontInput(label: 'uploadAngle' | 'replaceAngle') {
  return within(screen.getByRole('group', { name: 'front' })).getByLabelText(label);
}


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
beforeEach(() => { translation.locale = null; vi.mocked(toast.error).mockClear(); vi.mocked(fetch).mockReset(); client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); });
afterEach(() => { client.clear(); vi.useRealTimers(); });

it.each([false, true])('loads a saved preview on mount with stale=%s without drawing', async (stale) => {
  vi.mocked(fetch).mockResolvedValue(response({ ...saved, stale }) as Response);
  render(<OutfitPreview outfitId="one" />);
  expect(await screen.findByRole('img')).toHaveAttribute('src', saved.medium_url);
  expect(screen.queryByRole('button', { name: 'see' })).not.toBeInTheDocument();
  expect(Boolean(screen.queryByText('changed'))).toBe(stale);
  expect(screen.getByRole('button', { name: 'redraw' })).toBeVisible();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledWith('/api/v1/outfits/one/preview', expect.objectContaining({ method: 'GET' }));
});

it.each(['NO_PREVIEW', 'NO_PREVIEW_PHOTO'])('keeps the button after a missing preview: %s', async (detail) => {
  vi.mocked(fetch).mockResolvedValue(response({ detail }, 404) as Response);
  render(<OutfitPreview outfitId="one" />);
  await waitFor(() => expect(client.getQueryState(['outfit-preview', 'one'])?.fetchStatus).toBe('idle'));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'see' })).toBeEnabled();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
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
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'redraw' }));
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
  const input = frontInput('uploadAngle');
  await waitFor(() => expect(input).toBeEnabled());
  const file = new File(['photo'], 'photo.jpg', { type: 'image/jpeg' });
  fireEvent.change(input, { target: { files: [file] } });
  expect(await screen.findByRole('img')).toBeVisible();
  expect(input).toHaveValue('');
  expect((vi.mocked(fetch).mock.calls[1][1]?.body as FormData).get('image')).toEqual(file);
  fireEvent.change(frontInput('replaceAngle'), { target: { files: [file] } });
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
  fireEvent.change(frontInput('uploadAngle'), { target: { files: [new File(['bad'], 'bad.jpg')] } });
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

it('keeps failed removal open and clears its error after a successful upload', async () => {
  vi.mocked(fetch).mockImplementation(async (_url, options) => response(options?.method === 'DELETE' ? {} : saved, options?.method === 'DELETE' ? 502 : 200) as Response);
  render(<PreviewPhotoSettings />);
  await screen.findByRole('img');
  fireEvent.click(screen.getByText('remove'));
  fireEvent.click(screen.getByText('remove'));
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(screen.getByText('confirm')).toBeVisible();
  expect(screen.getByText('cancel')).toBeEnabled();
  fireEvent.change(frontInput('replaceAngle'), { target: { files: [new File(['photo'], 'photo.jpg')] } });
  await waitFor(() => expect(screen.getByText('remove')).toBeEnabled());
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
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
  fireEvent.change(frontInput('replaceAngle'), { target: { files: [new File(['photo'], 'photo.jpg')] } });
  await waitFor(() => expect(screen.getByRole('img', { name: 'title' }).closest('a')).toHaveAttribute('href', '/new-photo'));
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['preview-photo'] });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['outfit-preview'] });
  await waitFor(() => expect(screen.getByRole('img', { name: 'see' }).closest('a')).toHaveAttribute('href', '/new-photo'));
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

it('waits for a new created_at when another regenerate is in progress', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response({ ...saved, stale: true }) as Response)
    .mockResolvedValueOnce(response({ detail: 'PREVIEW_IN_PROGRESS' }, 409) as Response)
    .mockResolvedValueOnce(response(saved) as Response)
    .mockResolvedValue(response({ ...saved, created_at: '2026-10-25', medium_url: '/new-preview' }) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(screen.getByText('see'));
  await screen.findByRole('img');
  fireEvent.click(screen.getByText('redraw'));
  vi.useFakeTimers();
  await act(async () => { fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'redraw' })); await vi.advanceTimersByTimeAsync(0); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2001); });
  expect(screen.getByRole('status')).toHaveTextContent('drawing');
  expect(screen.getByRole('img')).toHaveAttribute('src', saved.medium_url);
  await act(async () => { await vi.advanceTimersByTimeAsync(2001); });
  expect(screen.getByRole('img')).toHaveAttribute('src', '/new-preview');
  expect(vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
});

it.each(['photo', 'outfit'])('handles a %s read failure without a global toast', async (kind) => {
  client.clear();
  client = createQueryClient();
  vi.mocked(fetch).mockResolvedValue(response({ detail: 'Unavailable' }, 503) as Response);
  render(kind === 'photo' ? <PreviewPhotoSettings /> : <OutfitPreview outfitId="one" />);
  if (kind === 'outfit') fireEvent.click(screen.getByText('see'));
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(toast.error).not.toHaveBeenCalled();
});

it('cancels polling on unmount before another GET', async () => {
  vi.useFakeTimers();
  vi.mocked(fetch).mockResolvedValueOnce(response({ detail: 'NO_PREVIEW' }, 404) as Response)
    .mockResolvedValueOnce(response({ detail: 'PREVIEW_IN_PROGRESS' }, 409) as Response)
    .mockResolvedValue(response({ detail: 'NO_PREVIEW' }, 404) as Response);
  const view = render(<OutfitPreview outfitId="one" />);
  await act(async () => { fireEvent.click(screen.getByText('see')); await vi.advanceTimersByTimeAsync(2001); });
  expect(fetch).toHaveBeenCalledTimes(3);
  view.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(fetch).toHaveBeenCalledTimes(3);
});

it('refreshes displayed signed URLs after nine minutes without a POST', async () => {
  vi.useFakeTimers();
  vi.mocked(fetch).mockResolvedValueOnce(response(saved) as Response)
    .mockResolvedValue(response({ ...saved, medium_url: '/renewed-url' }) as Response);
  render(<OutfitPreview outfitId="one" />);
  await act(async () => { fireEvent.click(screen.getByText('see')); await vi.advanceTimersByTimeAsync(1); });
  expect(screen.getByRole('img')).toHaveAttribute('src', saved.medium_url);
  await act(async () => { await vi.advanceTimersByTimeAsync(9 * 60_000 + 1); });
  expect(screen.getByRole('img')).toHaveAttribute('src', '/renewed-url');
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(vi.mocked(fetch).mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true);
});

it('shows three angle slots with independent upload and removal', async () => {
  const photos: Record<string, typeof saved> = { front: saved };
  vi.mocked(fetch).mockImplementation(async (url, options) => {
    const angle = new URL(String(url), 'https://test.local').searchParams.get('angle') ?? 'front';
    if (options?.method === 'PUT') photos[angle] = { ...saved, image_url: `/${angle}`, medium_url: `/${angle}-medium` };
    if (options?.method === 'DELETE') { delete photos[angle]; return response(null, 204) as Response; }
    return response({ ...photos.front, photos: { ...photos } }) as Response;
  });
  render(<PreviewPhotoSettings />);
  await screen.findByRole('img', { name: 'title' });
  expect(screen.getAllByRole('group')).toHaveLength(3);
  for (const angle of ['quarter', 'side']) {
    const input = within(screen.getByRole('group', { name: angle })).getByLabelText('uploadAngle');
    fireEvent.change(input, { target: { files: [new File(['photo'], `${angle}.jpg`)] } });
    expect(await screen.findByRole('img', { name: angle })).toHaveAttribute('src', `/${angle}-medium`);
    await waitFor(() => expect(input).toBeEnabled());
    expect(input).toHaveValue('');
    expect(fetch).toHaveBeenCalledWith(`/api/v1/users/me/preview-photo?angle=${angle}`, expect.objectContaining({ method: 'PUT' }));
  }
  const side = within(screen.getByRole('group', { name: 'side' }));
  fireEvent.click(side.getByText('remove'));
  expect(side.getByText('confirmAngle')).toBeVisible();
  fireEvent.click(side.getByText('remove'));
  await waitFor(() => expect(screen.queryByRole('img', { name: 'side' })).not.toBeInTheDocument());
  expect(screen.getByRole('img', { name: 'quarter' })).toBeVisible();
  expect(screen.getByRole('img', { name: 'title' })).toBeVisible();
  expect(fetch).toHaveBeenCalledWith('/api/v1/users/me/preview-photo?angle=side', expect.objectContaining({ method: 'DELETE' }));
});

it('shows optional photos without a front photo', async () => {
  vi.mocked(fetch).mockResolvedValue(response({ photos: { side: saved } }) as Response);
  render(<PreviewPhotoSettings />);
  expect(await screen.findByRole('img', { name: 'side' })).toBeVisible();
  expect(frontInput('uploadAngle')).toBeEnabled();
  expect(screen.queryByRole('img', { name: 'title' })).not.toBeInTheDocument();
});

it.each([{}, { photos: {} }, { photos: { side: {} } }, { photos: { back: saved } }])('rejects malformed photo collections %j', async (data) => {
  vi.mocked(fetch).mockResolvedValue(response(data) as Response);
  render(<PreviewPhotoSettings />);
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});


it.each([
  { locale: 'ja' as const, upload: '側面：写真をアップロード', replace: '正面：写真を変更' },
  { locale: 'zh-CN' as const, upload: '侧面：上传照片', replace: '正面：更换照片' },
  { locale: 'zh-TW' as const, upload: '側面：上傳照片', replace: '正面：更換照片' },
])('localizes angle upload and replace labels with ICU messages in $locale', async ({ locale, upload, replace }) => {
  translation.locale = locale;
  vi.mocked(fetch).mockResolvedValue(response({ photos: { front: saved } }) as Response);
  render(<PreviewPhotoSettings />);
  await screen.findByRole('img');
  for (const label of [upload, replace]) {
    expect(screen.getByText(label)).toBeVisible();
    expect(screen.getByLabelText(label)).toHaveAttribute('aria-label', label);
    expect(screen.getByLabelText(label)).toBeEnabled();
  }
});


it('sends the optional redraw note and resets it after cancel', async () => {
  vi.mocked(fetch).mockResolvedValue(response(saved) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(await screen.findByRole('button', { name: 'redraw' }));
  const note = screen.getByRole('textbox', { name: 'changeNote' });
  expect(note).toHaveAttribute('maxLength', '500');
  fireEvent.change(note, { target: { value: 'Looser sleeves' } });
  fireEvent.click(screen.getByRole('button', { name: 'cancel' }));
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'redraw' }));
  expect(screen.getByRole('textbox')).toHaveValue('');
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Looser sleeves' } });
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'redraw' }));
  await waitFor(() => expect(fetch).toHaveBeenLastCalledWith('/api/v1/outfits/one/preview?regenerate=true', expect.objectContaining({ method: 'POST', body: JSON.stringify({ note: 'Looser sleeves' }) })));
});

it('confirms deletion and returns to See it on me', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response(saved) as Response).mockResolvedValue(response(null, 204) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(await screen.findByRole('button', { name: 'delete' }));
  expect(screen.getByRole('dialog', { name: 'deleteConfirm' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'cancel' }));
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'delete' }));
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'delete' }));
  expect(await screen.findByRole('button', { name: 'see' })).toBeVisible();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/outfits/one/preview', expect.objectContaining({ method: 'DELETE' }));
});

it('keeps the saved preview when deletion fails', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response(saved) as Response).mockResolvedValue(response({ detail: 'PREVIEW_IN_PROGRESS' }, 409) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(await screen.findByRole('button', { name: 'delete' }));
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'delete' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('failed');
  expect(screen.getByRole('img')).toBeVisible();
});


it('rejects a noted redraw conflict instead of adopting another draw', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response(saved) as Response)
    .mockResolvedValueOnce(response({ detail: 'PREVIEW_IN_PROGRESS' }, 409) as Response)
    .mockResolvedValue(response({ ...saved, created_at: 'another-draw', medium_url: '/another.jpg' }) as Response);
  const { result } = renderHook(() => useOutfitPreview('one'), { wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> });
  await waitFor(() => expect(result.current.preview).toEqual(saved));
  vi.useFakeTimers();
  let outcome!: Promise<boolean>;
  await act(async () => { outcome = result.current.draw(true, 'Looser sleeves'); await vi.advanceTimersByTimeAsync(2001); });
  expect(await outcome).toBe(false);
  expect(result.current.conflict).toBe(true);
  expect(result.current.preview).toEqual(saved);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('restores the note dialog after a conflict and retries the same note', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response(saved) as Response)
    .mockResolvedValueOnce(response({ detail: 'PREVIEW_IN_PROGRESS' }, 409) as Response)
    .mockResolvedValue(response({ ...saved, created_at: 'another-draw', medium_url: '/another.jpg' }) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(await screen.findByRole('button', { name: 'redraw' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Looser sleeves' } });
  vi.useFakeTimers();
  await act(async () => {
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'redraw' }));
    await vi.advanceTimersByTimeAsync(2001);
  });
  expect(screen.getByRole('dialog')).toBeVisible();
  expect(screen.getByRole('textbox')).toHaveValue('Looser sleeves');
  expect(screen.getByRole('alert')).toHaveTextContent('redrawConflict');
  expect(fetch).toHaveBeenCalledTimes(2);
  await act(async () => {
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'redraw' }));
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/outfits/one/preview?regenerate=true', expect.objectContaining({ method: 'POST', body: JSON.stringify({ note: 'Looser sleeves' }) }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByRole('img')).toHaveAttribute('src', '/another.jpg');
});

it.each(['', 'Great fit'])('likes a saved preview with optional note %s', async (note) => {
  vi.mocked(fetch).mockResolvedValue(response(saved) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(await screen.findByRole('button', { name: 'like' }));
  const dialog = screen.getByRole('dialog');
  const input = within(dialog).getByRole('textbox', { name: 'likeNote' });
  expect(input).toHaveAttribute('maxlength', '500');
  fireEvent.change(input, { target: { value: note } });
  vi.mocked(fetch).mockResolvedValue(response({ ...saved, liked: true, like_note: note }) as Response);
  fireEvent.click(within(dialog).getByRole('button', { name: 'like' }));
  expect(await screen.findByRole('button', { name: 'liked' })).toHaveAttribute('aria-pressed', 'true');
  expect(fetch).toHaveBeenCalledWith('/api/v1/outfits/one/preview/feedback', expect.objectContaining({ method: 'POST', body: JSON.stringify({ kind: 'like', note, preview_created_at: saved.created_at }) }));
  fireEvent.click(screen.getByRole('button', { name: 'liked' }));
  expect(screen.getByRole('textbox', { name: 'likeNote' })).toHaveValue(note);
});

it('keeps the like note after a failed save', async () => {
  vi.mocked(fetch).mockResolvedValue(response(saved) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(await screen.findByRole('button', { name: 'like' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Good fit' } });
  vi.mocked(fetch).mockResolvedValue(response({ detail: 'Failed' }, 500) as Response);
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'like' }));
  expect(await screen.findByRole('textbox', { name: 'likeNote' })).toHaveValue('Good fit');
  expect(await screen.findByText('failed')).toBeVisible();
});


it('retains the note and reports a stale preview when liking from an older tab', async () => {
  vi.mocked(fetch).mockResolvedValue(response(saved) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(await screen.findByRole('button', { name: 'like' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Good fit' } });
  vi.mocked(fetch).mockResolvedValue(response({ detail: 'PREVIEW_CHANGED' }, 409) as Response);
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'like' }));
  expect(await screen.findByText('likeChanged')).toBeVisible();
  expect(screen.queryByText('failed')).not.toBeInTheDocument();
  expect(screen.getByRole('textbox')).toHaveValue('Good fit');
  expect(fetch).toHaveBeenCalledWith('/api/v1/outfits/one/preview/feedback', expect.objectContaining({ body: JSON.stringify({ kind: 'like', note: 'Good fit', preview_created_at: saved.created_at }) }));
});


it.each(['PREVIEW_CHANGED', 'PREVIEW_IN_PROGRESS', undefined])('keeps like 409 outside the generic preview error: %s', async (detail) => {
  vi.mocked(fetch).mockResolvedValue(response(saved) as Response);
  render(<OutfitPreview outfitId="one" />);
  fireEvent.click(await screen.findByRole('button', { name: 'like' }));
  vi.mocked(fetch).mockResolvedValue(response({ detail }, 409) as Response);
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'like' }));
  expect(await screen.findByRole('textbox', { name: 'likeNote' })).toBeVisible();
  expect(screen.queryByText('failed')).not.toBeInTheDocument();
});

it.each([
  [['clothes'], ['clothesChanged']],
  [['person'], ['personChanged']],
  [['clothes', 'person'], ['clothesChanged', 'personChanged']],
  [['unknown'], ['changed']],
  [[], []],
])('shows stale reasons %j', async (reasons, labels) => {
  vi.mocked(fetch).mockResolvedValue(response({ ...saved, stale: reasons.length > 0, stale_reasons: reasons }) as Response);
  render(<OutfitPreview outfitId="one" />);
  await screen.findByRole('img');
  for (const label of ['clothesChanged', 'personChanged', 'changed']) {
    expect(Boolean(screen.queryByText(label))).toBe(labels.includes(label));
  }
});

const outfitCatalogs = import.meta.glob('../messages/*/outfits.json', { eager: true, import: 'default' });
it.each(Object.entries(outfitCatalogs))('has stale labels in %s', (_path, catalog) => {
  const preview = (catalog as { preview: Record<string, string> }).preview;
  for (const key of ['clothesChanged', 'personChanged', 'changed']) {
    expect(preview[key]).toBeTruthy();
  }
  if (_path.includes('/en/')) {
    expect(preview.clothesChanged).toBe('Clothes changed');
    expect(preview.personChanged).toBe('Your photos or measurements changed');
    expect(preview.changed).toBe('Preview is out of date');
  }
});
