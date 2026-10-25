'use client';

import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { api, ApiError, API_BASE_PATH, getAccessToken } from '@/lib/api';
import { useSetTokenIfAvailable } from '@/lib/hooks/use-session-token';
import { queryKeys } from '@/lib/hooks/query-keys';

export type PreviewAngle = 'front' | 'quarter' | 'side';
export const previewAngles: PreviewAngle[] = ['front', 'quarter', 'side'];
type PreviewPhotos = Partial<Record<PreviewAngle, PreviewPhoto>>;
export interface PreviewPhoto { image_url: string; medium_url: string }
export interface OutfitPreview extends PreviewPhoto { created_at: string; stale: boolean }

export function previewErrorCode(error: unknown): string | undefined {
  if (!error || !(error instanceof ApiError) || !error.data || typeof error.data !== 'object') return;
  const detail = (error.data as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (!detail || typeof detail !== 'object') return;
  const { code, error_code } = detail as { code?: unknown; error_code?: unknown };
  return typeof code === 'string' ? code : typeof error_code === 'string' ? error_code : undefined;
}

function checkedPhoto<Value extends PreviewPhoto>(value: Value): Value {
  if (!value || typeof value.image_url !== 'string' || !value.image_url) throw new Error('Invalid photo');
  return value;
}

function checkedPhotos(value: { photos?: PreviewPhotos } & Partial<PreviewPhoto>): PreviewPhotos {
  if (value?.photos && typeof value.photos === 'object' && !Array.isArray(value.photos)) {
    const entries = Object.entries(value.photos);
    if (!entries.length) throw new Error('Invalid photos');
    for (const [angle, photo] of entries) {
      if (!previewAngles.includes(angle as PreviewAngle)) throw new Error('Invalid angle');
      checkedPhoto(photo);
    }
    return value.photos;
  }
  return { front: checkedPhoto(value as PreviewPhoto) };
}

export function usePreviewPhoto() {
  const { status, data: session } = useSession();
  useSetTokenIfAvailable();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.previewPhoto, retry: false, gcTime: 0, meta: { toastsOwnErrors: true },
    enabled: status !== 'loading',
    queryFn: async () => {
      try { return checkedPhotos(await api.get<{ photos?: PreviewPhotos } & Partial<PreviewPhoto>>('/users/me/preview-photo')); }
      catch (error) {
        if (error instanceof ApiError && error.status === 404 && previewErrorCode(error) === 'NO_PREVIEW_PHOTO') return null;
        throw error;
      }
    },
  });
  const refresh = async () => {
    await client.cancelQueries({ queryKey: queryKeys.outfitPreviews });
    await client.resetQueries({ queryKey: queryKeys.outfitPreviews });
    await Promise.all([
      client.invalidateQueries({ queryKey: queryKeys.previewPhoto }),
      client.invalidateQueries({ queryKey: queryKeys.outfitPreviews }),
    ]);
  };
  const upload = useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: async ({ file, angle }: { file: File; angle: PreviewAngle }) => {
      const body = new FormData();
      body.append('image', file);
      const token = session?.accessToken || getAccessToken();
      const response = await fetch(`${API_BASE_PATH}/users/me/preview-photo${angle === 'front' ? '' : `?angle=${angle}`}`, {
        method: 'PUT', body, credentials: 'include',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) throw new ApiError('Photo update failed', response.status, await response.json());
      return checkedPhotos(await response.json());
    },
    onSuccess: refresh,
  });
  const remove = useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: async (angle: PreviewAngle) => { await api.delete(`/users/me/preview-photo${angle === 'front' ? '' : `?angle=${angle}`}`); },
    onSuccess: refresh,
  });
  return { photo: query.data?.front, photos: query.data, loading: query.isPending, pending: upload.isPending || remove.isPending,
    error: query.isError || upload.isError || remove.isError,
    save: async (file?: File, angle: PreviewAngle = 'front') => {
      upload.reset(); remove.reset();
      try { if (file) await upload.mutateAsync({ file, angle }); else await remove.mutateAsync(angle); return true; }
      catch { return false; }
    } };
}

function pausePoll(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new Error('Preview cancelled')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, 2000);
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
  });
}

export function useOutfitPreview(outfitId: string) {
  useSetTokenIfAvailable();
  const client = useQueryClient();
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => { active.current?.abort(); }, []);
  const key = queryKeys.outfitPreview(outfitId);
  const read = async (signal?: AbortSignal) => {
    try { return checkedPhoto(await api.get<OutfitPreview>(`/outfits/${outfitId}/preview`, { signal })); }
    catch (error) {
      if (error instanceof ApiError && error.status === 404 && previewErrorCode(error) === 'NO_PREVIEW') return null;
      throw error;
    }
  };
  const query = useQuery({
    queryKey: key, queryFn: ({ signal }) => read(signal),
    enabled: (current) => Boolean(current.state.data),
    staleTime: 9 * 60_000, refetchInterval: 9 * 60_000,
    refetchIntervalInBackground: true, retry: false, gcTime: 0,
    meta: { toastsOwnErrors: true },
  });
  const mutation = useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: async ({ regenerate, signal }: { regenerate: boolean; signal: AbortSignal }) => {
      const previousCreatedAt = client.getQueryData<OutfitPreview>(key)?.created_at;
      if (!regenerate) {
        const saved = await client.fetchQuery({ queryKey: key, queryFn: ({ signal: querySignal }) => read(querySignal), staleTime: 0, retry: false, meta: { toastsOwnErrors: true } });
        if (saved) return saved;
      }
      if (signal.aborted) throw new Error('Preview cancelled');
      try { return checkedPhoto(await api.post<OutfitPreview>(`/outfits/${outfitId}/preview${regenerate ? '?regenerate=true' : ''}`, undefined, { signal })); }
      catch (error) {
        if (!(error instanceof ApiError && error.status === 409 && previewErrorCode(error) === 'PREVIEW_IN_PROGRESS')) throw error;
      }
      const deadline = Date.now() + 200_000;
      while (Date.now() < deadline) {
        await pausePoll(signal);
        const saved = await read(signal);
        if (saved && (!regenerate || (typeof saved.created_at === 'string' && saved.created_at !== previousCreatedAt))) return saved;
      }
      throw new Error('Preview timed out');
    },
    onSuccess: (preview, { signal }) => { if (!signal.aborted) client.setQueryData(key, preview); },
  });
  const draw = async (regenerate = false) => {
    if (active.current) return false;
    const controller = new AbortController();
    active.current = controller;
    const cancelRead = () => { void client.cancelQueries({ queryKey: key }); };
    controller.signal.addEventListener('abort', cancelRead, { once: true });
    const timeout = setTimeout(() => controller.abort(), 200_000);
    try { await mutation.mutateAsync({ regenerate, signal: controller.signal }); return true; }
    catch { return false; }
    finally { clearTimeout(timeout); controller.signal.removeEventListener('abort', cancelRead); if (active.current === controller) active.current = null; }
  };
  return { preview: query.data, pending: mutation.isPending, error: mutation.isError && previewErrorCode(mutation.error) !== 'NO_PREVIEW_PHOTO',
    missingPhoto: previewErrorCode(mutation.error) === 'NO_PREVIEW_PHOTO', draw };
}
