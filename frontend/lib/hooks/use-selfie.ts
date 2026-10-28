'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { API_BASE_PATH, ApiError, getAccessToken } from '@/lib/api';
import { useSetTokenIfAvailable } from '@/lib/hooks/use-session-token';
import { invalidateOutfitCaches, invalidateEveryItemWearCache } from '@/lib/hooks/cache-invalidation';
import { queryKeys } from '@/lib/hooks/query-keys';
import type { Outfit } from '@/lib/hooks/use-outfits';
import type { Item } from '@/lib/types';

export interface SelfieResult {
  matches: Item[];
  considered: number;
}

export function useSelfieRecognition() {
  const { data: session } = useSession();
  return useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append('image', file);
      const token = session?.accessToken || getAccessToken();
      const headers: Record<string, string> = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const response = await fetch(`${API_BASE_PATH}/selfie`, {
        method: 'POST',
        body: formData,
        credentials: 'include',
        headers,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => {
          throw new ApiError('Invalid server response', response.status, null);
        });
        throw new ApiError(
          typeof data.detail === 'string' ? data.detail : 'Request failed',
          response.status,
          data
        );
      }
      return response.json() as Promise<SelfieResult>;
    },
  });
}


export function useConfirmSelfie() {
  const client = useQueryClient();
  useSetTokenIfAvailable();
  const { data: session } = useSession();
  return useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: async (payload: { request_id: string; item_ids: string[]; image: File }) => {
      const body = new FormData();
      body.append('request_id', payload.request_id);
      body.append('item_ids', JSON.stringify(payload.item_ids));
      body.append('image', payload.image);
      const token = session?.accessToken || getAccessToken();
      const response = await fetch(`${API_BASE_PATH}/selfie/confirm-photo`, {
        method: 'POST', body, credentials: 'include',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) throw new ApiError('Photo confirmation failed', response.status, await response.json());
      return response.json() as Promise<Outfit>;
    },
    onSuccess: () => {
      invalidateOutfitCaches(client);
      invalidateEveryItemWearCache(client);
      client.invalidateQueries({ queryKey: queryKeys.learning.all });
    },
  });
}
