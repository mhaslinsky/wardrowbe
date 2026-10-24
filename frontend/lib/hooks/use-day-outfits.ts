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

export function useLogDayWear(toastsOwnErrors = false) {
  useSetTokenIfAvailable();
  const queryClient = useQueryClient();
  return useMutation({
    meta: { toastsOwnErrors },
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

export function useUndoDayWear() {
  useSetTokenIfAvailable();
  const queryClient = useQueryClient();
  return useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: ({ outfitId, washedItemIds }: { outfitId: string; washedItemIds?: string[] }) =>
      api.post<Outfit & { learning_stale: boolean }>(`/outfits/${outfitId}/undo-wear`, {
        ...(washedItemIds !== undefined ? { washed_item_ids: washedItemIds } : {}),
      }),
    onSuccess: (_, { outfitId }) => {
      invalidateOutfitCaches(queryClient, outfitId);
      queryClient.invalidateQueries({ queryKey: queryKeys.learning.all });
      invalidateEveryItemWearCache(queryClient);
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
