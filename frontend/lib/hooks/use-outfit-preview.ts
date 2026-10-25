'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, API_BASE_PATH, getAccessToken } from '@/lib/api';
import { useSetTokenIfAvailable } from '@/lib/hooks/use-session-token';

export interface PreviewPhoto { image_url: string; medium_url: string }
export interface OutfitPreview extends PreviewPhoto { created_at: string; stale: boolean }
const photoChanged = 'preview-photo-changed';

function checkedPhoto<Value extends PreviewPhoto>(value: Value): Value {
  if (!value || typeof value.image_url !== 'string' || !value.image_url) throw new Error('Invalid photo');
  return value;
}

export function usePreviewPhoto() {
  useSetTokenIfAvailable();
  const [photo, setPhoto] = useState<PreviewPhoto | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    api.get<PreviewPhoto>('/users/me/preview-photo').then((value) => {
      if (active) setPhoto(checkedPhoto(value));
    }).catch((failure) => {
      if (active && !(failure instanceof ApiError && failure.status === 404)) setError(true);
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  const save = async (file?: File) => {
    setPending(true);
    setError(false);
    try {
      if (file) {
        const body = new FormData();
        body.append('image', file);
        const token = getAccessToken();
        const response = await fetch(`${API_BASE_PATH}/users/me/preview-photo`, {
          method: 'PUT', body, credentials: 'include',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!response.ok) throw new Error('Upload failed');
        setPhoto(checkedPhoto(await response.json()));
      } else {
        await api.delete('/users/me/preview-photo');
        setPhoto(null);
      }
      window.dispatchEvent(new Event(photoChanged));
    } catch { setError(true); }
    finally { setPending(false); }
  };
  return { photo, loading, pending, error, save };
}

export function useOutfitPreview(outfitId: string) {
  useSetTokenIfAvailable();
  const [preview, setPreview] = useState<OutfitPreview | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  const [missingPhoto, setMissingPhoto] = useState(false);
  const generation = useRef(0);
  useEffect(() => {
    const reset = () => { generation.current += 1; setPreview(null); setMissingPhoto(false); setError(false); setPending(false); };
    reset();
    window.addEventListener(photoChanged, reset);
    return () => { generation.current += 1; window.removeEventListener(photoChanged, reset); };
  }, [outfitId]);
  const draw = useCallback(async (regenerate = false) => {
    if (pending) return;
    const requestGeneration = generation.current;
    setPending(true);
    setError(false);
    setMissingPhoto(false);
    try {
      let saved: OutfitPreview;
      if (regenerate) {
        saved = await api.post<OutfitPreview>(`/outfits/${outfitId}/preview?regenerate=true`);
      } else {
        try { saved = await api.get<OutfitPreview>(`/outfits/${outfitId}/preview`); }
        catch (failure) {
          if (!(failure instanceof ApiError && failure.status === 404)) throw failure;
          saved = await api.post<OutfitPreview>(`/outfits/${outfitId}/preview`);
        }
      }
      if (generation.current === requestGeneration) setPreview(checkedPhoto(saved));
    } catch (failure) {
      if (generation.current !== requestGeneration) return;
      if (failure instanceof ApiError && failure.status === 409 && JSON.stringify(failure.data).includes('NO_PREVIEW_PHOTO')) setMissingPhoto(true);
      else setError(true);
    } finally { if (generation.current === requestGeneration) setPending(false); }
  }, [outfitId, pending]);
  return { preview, pending, error, missingPhoto, draw };
}
