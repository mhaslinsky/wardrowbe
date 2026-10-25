'use client';

import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { api, ApiError, API_BASE_PATH, getAccessToken } from '@/lib/api';
import { useSetTokenIfAvailable } from '@/lib/hooks/use-session-token';
import { queryKeys } from '@/lib/hooks/query-keys';

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

export function usePreviewPhoto() {
  const { status, data: session } = useSession();
  useSetTokenIfAvailable();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.previewPhoto, retry: false, gcTime: 0,
    enabled: status !== 'loading',
    queryFn: async () => {
      try { return checkedPhoto(await api.get<PreviewPhoto>('/users/me/preview-photo')); }
      catch (error) {
        if (error instanceof ApiError && error.status === 404 && previewErrorCode(error) === 'NO_PREVIEW_PHOTO') return null;
        throw error;
      }
    },
  });
  const refresh = async (photo: PreviewPhoto | null) => {
    await client.cancelQueries({ queryKey: queryKeys.outfitPreviews });
    await client.resetQueries({ queryKey: queryKeys.outfitPreviews });
    client.setQueryData(queryKeys.previewPhoto, photo);
    await Promise.all([
      client.invalidateQueries({ queryKey: queryKeys.previewPhoto }),
      client.invalidateQueries({ queryKey: queryKeys.outfitPreviews }),
    ]);
  };
  const upload = useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: async (file: File) => {
      const body = new FormData();
      body.append('image', file);
      const token = session?.accessToken || getAccessToken();
      const response = await fetch(`${API_BASE_PATH}/users/me/preview-photo`, {
        method: 'PUT', body, credentials: 'include',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) throw new ApiError('Photo update failed', response.status, await response.json());
      return checkedPhoto(await response.json() as PreviewPhoto);
    },
    onSuccess: refresh,
  });
  const remove = useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: async () => { await api.delete('/users/me/preview-photo'); return null; },
    onSuccess: refresh,
  });
  return { photo: query.data, loading: query.isPending, pending: upload.isPending || remove.isPending,
    error: query.isError || upload.isError || remove.isError,
    save: async (file?: File) => {
      try { if (file) await upload.mutateAsync(file); else await remove.mutateAsync(); return true; }
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
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => read(signal), enabled: false, retry: false, gcTime: 0 });
  const mutation = useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: async ({ regenerate, signal }: { regenerate: boolean; signal: AbortSignal }) => {
      if (!regenerate) {
        const saved = await client.fetchQuery({ queryKey: key, queryFn: ({ signal: querySignal }) => read(querySignal), staleTime: 0, retry: false });
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
        if (saved) return saved;
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
