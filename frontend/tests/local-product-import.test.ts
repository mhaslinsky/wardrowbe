import { beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError } from '@/lib/api';
import { localProductImport } from '@/lib/local-product-import';
import { confirmedFilePayload } from '@/lib/extension-reader/confirmation';
import * as captureModule from '@/lib/extension-reader/capture';
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), api: { post: vi.fn(), patch: vi.fn(), get: vi.fn() } }));
const capture = { source_url: 'https://shop.example/product', name: 'Shirt', images: ['https://shop.example/image.png'], sizes: [], colors: [] };
const item = { id: 'item', user_id: 'user', tags: {}, notes: null };
let owner: string | undefined;
function transport(file?: File) {
  return localProductImport('user', file ? { ...capture, source_url: '', images: [] } : capture, file, file ? 'blob:preview' : undefined, vi.fn(), () => owner);
}
beforeEach(() => {
  vi.clearAllMocks(); sessionStorage.clear(); owner = 'user';
  vi.mocked(api.post).mockResolvedValue({ state: 'considering', item });
  vi.mocked(api.patch).mockResolvedValue(item);
  vi.mocked(api.get).mockImplementation(async path => path === '/considering' ? [{ state: 'considering', item }] : item);
});
it('loads and cancels entirely in memory before Save', async () => {
  const local = transport();
  expect(await local.request('load')).toEqual({ capture, partial: false, uncertain: false });
  expect(sessionStorage.length).toBe(0);
  await local.request('cancel');
  expect(api.post).not.toHaveBeenCalled(); expect(api.patch).not.toHaveBeenCalled(); expect(api.get).not.toHaveBeenCalled();
});
it('loads all extracted image candidates before applying the Save limit', async () => {
  const input = { ...capture, images: Array.from({ length: 30 }, (_, index) => `https://shop.example/${index}.png`) };
  const local = localProductImport('user', input, undefined, undefined, vi.fn(), () => owner);
  const loaded = await local.request('load');
  expect(local.validate(loaded.capture).images).toHaveLength(30);
});
it('sends a File and metadata only, without blob or placeholder URLs', async () => {
  const file = new File(['image'], 'shirt.png', { type: 'image/png' });
  const local = transport(file);
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ state: 'considering', item }), { status: 201 }));
  expect(await local.request('save', { ...capture, source_url: '', images: ['blob:preview'] })).toEqual({ saved: true, item: 'item' });
  const body = vi.mocked(fetch).mock.calls[0][1]!.body as FormData;
  expect(body.get('image')).toBe(file);
  const metadata = String(body.get('metadata'));
  expect(metadata).not.toMatch(/blob:|wardrowbe.invalid|image_urls|source_url/);
  expect(sessionStorage.length).toBe(0);
});
it('keeps preview URLs outside capture validation and payloads', () => {
  const validate = vi.spyOn(captureModule, 'validateCapture');
  const payload = confirmedFilePayload({ ...capture, source_url: '', images: ['blob:preview'] });
  expect(JSON.stringify(validate.mock.calls)).not.toContain('blob:');
  expect(JSON.stringify(payload)).not.toMatch(/blob:|wardrowbe.invalid/);
  validate.mockRestore();
});
it('allows retry after an explicit 422 upload rejection', async () => {
  const local = transport(new File(['image'], 'shirt.png', { type: 'image/png' }));
  vi.mocked(fetch).mockResolvedValue(new Response('{}', { status: 422 }));
  await expect(local.request('save', { ...capture, source_url: '', images: [] })).rejects.toBeInstanceOf(ApiError);
  expect((await local.request('load')).uncertain).toBe(false);
});
it('does not repeat a create after an unknown network outcome, including reload', async () => {
  const local = transport(); vi.mocked(api.post).mockRejectedValue(Error('network'));
  await expect(local.request('save', capture)).rejects.toThrow('network');
  const recovered = transport(); expect((await recovered.request('load')).uncertain).toBe(true);
  await expect(recovered.request('save', capture)).rejects.toThrow('uncertain');
  await expect(recovered.request('cancel')).rejects.toThrow('partial');
  expect(api.post).toHaveBeenCalledTimes(1);
  await recovered.request('discard'); expect(sessionStorage.length).toBe(0);
});
it('recovers a partial save and retries metadata without creating again', async () => {
  vi.mocked(api.patch).mockRejectedValueOnce(Error('details'));
  await expect(transport().request('save', capture)).rejects.toThrow('details');
  const recovered = transport(); expect((await recovered.request('load')).partial).toBe(true);
  expect(await recovered.request('save', capture)).toEqual({ saved: true, item: 'item' });
  expect(api.post).toHaveBeenCalledTimes(1); expect(api.patch).toHaveBeenCalledTimes(2);
});
it('stops requests when the signed-in account changes', async () => {
  const local = transport(); owner = 'other';
  await expect(local.request('save', capture)).rejects.toThrow('identity');
  expect(api.post).not.toHaveBeenCalled();
});
it('checks identity after the metadata reread before requesting the shelf', async () => {
  vi.mocked(api.get).mockImplementation(async () => { owner = 'other'; return item; });
  await expect(transport().request('save', capture)).rejects.toThrow('identity');
  expect(api.get).toHaveBeenCalledTimes(1);
});
