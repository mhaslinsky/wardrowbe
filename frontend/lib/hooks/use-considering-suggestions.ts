'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { api } from '@/lib/api';
import type { Outfit } from '@/lib/types';
import { queryKeys } from './query-keys';
import { applySessionToken } from './use-session-token';

export function useConsideringSuggestions(itemId: string) {
  const { data: session } = useSession();
  const client = useQueryClient();
  return useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: async (signal: AbortSignal) => {
      applySessionToken(session);
      const outfits = await api.post<Outfit[]>(`/considering/${itemId}/suggestions`, {}, { signal });
      if (!Array.isArray(outfits) || !outfits.length) throw new Error('Empty suggestions');
      return outfits;
    },
    onSuccess: (outfits, signal) => {
      if (signal.aborted) return;
      for (const outfit of outfits) client.setQueryData(queryKeys.outfit(outfit.id), outfit);
      client.invalidateQueries({ queryKey: queryKeys.outfits.all });
    },
  });
}
