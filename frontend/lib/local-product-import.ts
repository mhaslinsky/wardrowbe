import { api, ApiError, getAccessToken } from './api';
import { validateCapture } from './extension-reader/capture';
import { confirmedFilePayload, confirmedPayload, type ConfirmedProduct } from './extension-reader/confirmation';
import type { ImportTransport } from '@/components/considering-import';
import type { Item } from './types';

type Draft = { capture: ConfirmedProduct; fileBacked: boolean; creating?: boolean; item?: string; update?: ReturnType<typeof confirmedPayload>['update'] };
export function localProductImport(owner: string, initial: ConfirmedProduct, file: File | undefined, preview: string | undefined, finished: () => void, currentOwner: () => string | undefined): ImportTransport {
  const key = `considering-import:${owner}`;
  let draft: Draft = { capture: initial, fileBacked: !!file };
  const stored = sessionStorage.getItem(key);
  if (stored) draft = JSON.parse(stored);
  let busy = false;
  const persist = () => sessionStorage.setItem(key, JSON.stringify(draft));
  const identity = () => { if (currentOwner() !== owner) throw Error('identity'); };
  const validate = (input: unknown, saving = false) => {
    if (!draft.fileBacked) { const result = validateCapture(input); if (saving) confirmedPayload(input); return result; }
    confirmedFilePayload(input as ConfirmedProduct);
    return { ...input as ConfirmedProduct, images: [] };
  };
  function itemResult(value: Item, id?: string) {
    if (!value || typeof value.id !== 'string' || value.user_id !== owner || (id && value.id !== id)) throw Error('identity');
    return value;
  }
  return { preview, validate, request: async (action, capture) => {
    identity();
    if (action === 'load') return { capture: draft.capture, partial: !!draft.item, uncertain: !!draft.creating };
    if (busy) throw Error('busy');
    if (action === 'cancel' || action === 'discard') {
      if (action === 'cancel' && (draft.item || draft.creating)) throw Error('partial');
      sessionStorage.removeItem(key); finished(); return {};
    }
    if (draft.creating) throw Error('uncertain');
    busy = true;
    try {
      if (!draft.item) {
        if (!capture || (draft.fileBacked && !file)) throw Error('invalid');
        const payload = draft.fileBacked ? confirmedFilePayload(capture) : confirmedPayload(capture);
        draft.capture = validate(capture); draft.creating = true; persist();
        let created: { state: string; item: Item };
        try {
          if (draft.fileBacked) {
            const body = new FormData(); body.set('image', file!); body.set('metadata', JSON.stringify(payload.create));
            const token = getAccessToken();
            const response = await fetch('/api/v1/considering/upload', { method: 'POST', body, credentials: 'include', headers: token ? { Authorization: `Bearer ${token}` } : {} });
            if (!response.ok) throw new ApiError('upload', response.status, {});
            created = await response.json();
          } else created = await api.post('/considering', payload.create);
        } catch (failure) {
          if (failure instanceof ApiError && [400, 401, 403, 404, 409, 413, 422, 429].includes(failure.status)) { draft.creating = false; persist(); }
          throw failure;
        }
        identity();
        const item = itemResult(created.item);
        if (created.state !== 'considering') throw Error('response');
        draft.item = item.id; draft.update = payload.update; draft.creating = false; persist();
      }
      identity();
      await api.patch(`/items/${draft.item}`, draft.update);
      identity();
      const item = itemResult(await api.get(`/items/${draft.item}`), draft.item);
      identity();
      for (const [field, value] of Object.entries(draft.update!.tags)) {
        if (value !== undefined && JSON.stringify(item.tags?.[field as keyof typeof item.tags]) !== JSON.stringify(value)) throw Error('partial');
      }
      if (draft.update!.notes !== undefined && item.notes !== draft.update!.notes) throw Error('partial');
      const shelf = await api.get<{ state: string; item: Item }[]>('/considering');
      identity();
      if (!Array.isArray(shelf) || !shelf.some(entry => entry.state === 'considering' && entry.item.id === draft.item && entry.item.user_id === owner)) throw Error('response');
      sessionStorage.removeItem(key); return { saved: true, item: item.id };
    } finally { busy = false; }
  } };
}
