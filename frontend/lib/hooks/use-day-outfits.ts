import { useState } from 'react';
import { useUserProfile, useUserToday } from '@/lib/hooks/use-user';
import type { SuggestRequest } from '@/lib/types';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '@/lib/api';
import { useInfiniteOutfits, type Outfit } from '@/lib/hooks/use-outfits';
import { useSetTokenIfAvailable } from '@/lib/hooks/use-session-token';
import { invalidateEveryItemWearCache, invalidateOutfitCaches } from '@/lib/hooks/cache-invalidation';
import { queryKeys } from '@/lib/hooks/query-keys';

export function useDayOutfits(date: string) {
  return useInfiniteOutfits({ date_from: date, date_to: date });
}

export function useLogDayWear() {
  useSetTokenIfAvailable();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ outfitId, date, itemIds }: { outfitId: string; date: string; itemIds?: string[] }) =>
      api.post<Outfit & { already_logged: boolean }>(`/outfits/${outfitId}/wore`, {
        date, ...(itemIds ? { item_ids: itemIds } : {}),
      }),
    onSuccess: (outfit, { outfitId }) => {
      invalidateOutfitCaches(queryClient, outfitId);
      queryClient.invalidateQueries({ queryKey: queryKeys.outfit(outfit.id) });
      queryClient.invalidateQueries({ queryKey: queryKeys.learning.all });
      invalidateEveryItemWearCache(queryClient);
    },
  });
}

export type WashedItem = { id: string; name: string | null; washed_at: string };

function washedItems(error: unknown): WashedItem[] | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = (error.data as { detail?: { error_code?: string; items?: unknown } } | null)?.detail;
  if (detail?.error_code !== 'WASH_SINCE_WEAR' || !Array.isArray(detail.items) || !detail.items.length) return null;
  return detail.items.every((item) => item && typeof item.id === 'string' && (item.name === null || typeof item.name === 'string') && typeof item.washed_at === 'string')
    ? detail.items : null;
}

export function useUndoDayWear() {
  useSetTokenIfAvailable();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ outfitId, washedItemIds }: { outfitId: string; washedItemIds?: string[] }) => {
      try {
        return await api.post<Outfit & { learning_stale: boolean }>(`/outfits/${outfitId}/undo-wear`, {
          ...(washedItemIds !== undefined ? { washed_item_ids: washedItemIds } : {}),
        });
      } catch (error) {
        const items = washedItems(error);
        if (items) return { washedItems: items };
        throw error;
      }
    },
    onSuccess: (result, { outfitId }) => {
      if ('washedItems' in result) return;
      invalidateOutfitCaches(queryClient, outfitId);
      queryClient.invalidateQueries({ queryKey: queryKeys.learning.all });
      invalidateEveryItemWearCache(queryClient);
      queryClient.invalidateQueries({ queryKey: queryKeys.washHistory('').slice(0, 1) });
    },
  });
}

export async function loggedOutfitName(error: unknown): Promise<string | null> {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = (error.data as { detail?: { error_code?: string; existing_ids?: string[] } })?.detail;
  if (detail?.error_code !== 'DAY_LOGGED' || !detail.existing_ids?.[0]) return null;
  const outfit = await api.get<Outfit>(`/outfits/${detail.existing_ids[0]}`);
  return outfit.name || outfit.occasion;
}

export function useDaySelection() {
  const { data: profile } = useUserProfile();
  const today = useUserToday()();
  const [selectedDate, setDate] = useState<string | null>(null);
  return { date: selectedDate ?? today, today, setDate, ready: !!profile };
}

export function useGenerateDayOptions() {
  useSetTokenIfAvailable();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: SuggestRequest & { date: string }) =>
      api.post<Outfit[]>('/outfits/suggest-options', request),
    onSuccess: () => invalidateOutfitCaches(queryClient),
  });
}
