import { api } from '@/lib/api';
import type { BulkOperationParams } from '@/lib/hooks/use-items';

export interface PhotoRedrawResult {
  id: string;
  status?: string | null;
  error?: string | null;
}
interface PhotoRedrawBatch {
  results: PhotoRedrawResult[];
  has_more: boolean;
  next_cursor?: string | null;
}
function checkBatch(batch: PhotoRedrawBatch, ids?: string[]) {
  if (!Array.isArray(batch.results) || typeof batch.has_more !== 'boolean' ||
      batch.results.some((result) => !result || typeof result.id !== 'string' || (!result.status && !result.error))) {
    throw new Error('Invalid redraw response');
  }
  if (ids && (batch.has_more || batch.results.length !== ids.length ||
      new Set(batch.results.map((result) => result.id)).size !== ids.length ||
      batch.results.some((result) => !ids.includes(result.id)))) throw new Error('Incomplete redraw response');
}

export type RedrawSelection = BulkOperationParams & { retry: boolean };

export async function redrawPhotos(params: RedrawSelection): Promise<PhotoRedrawResult[]> {
  const path = '/items/bulk/redraw-photo';
  const results: PhotoRedrawResult[] = [];
  if (params.item_ids !== undefined) {
    const ids = Array.from(new Set(params.item_ids));
    if (!ids.length) throw new Error('Empty redraw selection');
    for (let offset = 0; offset < ids.length; offset += 20) {
      const chunk = ids.slice(offset, offset + 20);
      const batch = await api.post<PhotoRedrawBatch>(path, { ...params, item_ids: chunk });
      checkBatch(batch, chunk);
      results.push(...batch.results);
    }
    return results;
  }
  if (!params.select_all) throw new Error('Missing redraw selection');
  const seen = new Set<string>();
  let after_id: string | undefined;
  for (let count = 0; count < 200; count++) {
    const batch = await api.post<PhotoRedrawBatch>(path, { ...params, ...(after_id ? { after_id } : {}) });
    checkBatch(batch);
    results.push(...batch.results);
    if (!batch.has_more) return results;
    if (!batch.next_cursor || seen.has(batch.next_cursor)) throw new Error('Invalid redraw cursor');
    seen.add(batch.next_cursor);
    after_id = batch.next_cursor;
  }
  throw new Error('Redraw batch limit reached');
}
