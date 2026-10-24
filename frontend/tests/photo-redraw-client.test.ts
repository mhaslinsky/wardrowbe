import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import { redrawPhotos } from '@/lib/photo-redraw';
vi.mock('@/lib/api', async (original) => ({ ...(await original<typeof import('@/lib/api')>()), api: { post: vi.fn() } }));
beforeEach(() => vi.clearAllMocks());
it('chunks explicit selections at 20, deduplicates and preserves retry confirmation', async () => {
  vi.mocked(api.post).mockResolvedValue({ results: [], has_more: false });
  const ids = Array.from({ length: 21 }, (_, index) => String(index));
  await redrawPhotos({ item_ids: [...ids, '0'], retry: true });
  expect(api.post).toHaveBeenCalledTimes(2);
  expect(api.post).toHaveBeenNthCalledWith(1, '/items/bulk/redraw-photo', { item_ids: ids.slice(0, 20), retry: true });
  expect(api.post).toHaveBeenNthCalledWith(2, '/items/bulk/redraw-photo', { item_ids: ['20'], retry: true });
});
it('follows cursors with filters and exclusions and keeps partial failures', async () => {
  vi.mocked(api.post).mockResolvedValueOnce({ results: [{ id: 'one', status: 'generated' }], has_more: true, next_cursor: 'one' })
    .mockResolvedValueOnce({ results: [{ id: 'two', error: 'Confirm a paid retry' }], has_more: false });
  const params = { select_all: true, excluded_ids: ['skip'], filters: { type: 'shirt' }, retry: false };
  expect(await redrawPhotos(params)).toEqual([{ id: 'one', status: 'generated' }, { id: 'two', error: 'Confirm a paid retry' }]);
  expect(api.post).toHaveBeenLastCalledWith('/items/bulk/redraw-photo', { ...params, after_id: 'one' });
});
it('rejects an incomplete cursor response instead of reporting success', async () => {
  vi.mocked(api.post).mockResolvedValue({ results: [], has_more: true });
  await expect(redrawPhotos({ select_all: true, retry: false })).rejects.toThrow();
  expect(api.post).toHaveBeenCalledTimes(1);
});
it('rejects a repeated cursor without repeating a paid batch', async () => {
  vi.mocked(api.post).mockResolvedValue({ results: [], has_more: true, next_cursor: 'same' });
  await expect(redrawPhotos({ select_all: true, retry: false })).rejects.toThrow();
  expect(api.post).toHaveBeenCalledTimes(2);
});
it.each([{}, { item_ids: [] }])('rejects missing or empty selections', async (params) => {
  await expect(redrawPhotos({ ...params, retry: false })).rejects.toThrow();
  expect(api.post).not.toHaveBeenCalled();
});
it('does not retry a transport failure that may have charged', async () => {
  vi.mocked(api.post).mockRejectedValue(new Error('network'));
  await expect(redrawPhotos({ item_ids: ['one'], retry: false })).rejects.toThrow('network');
  expect(api.post).toHaveBeenCalledTimes(1);
});
