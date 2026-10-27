'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { api } from '@/lib/api';
import { queryKeys } from './query-keys';
import type { Item } from '@/lib/types';
import { invalidateItemCaches, invalidateOutfitCaches } from './cache-invalidation';
import { applySessionToken, useSetTokenIfAvailable } from './use-session-token';

export interface ConsideringEntry {
  state: 'considering' | 'passed' | 'bought';
  passed_reason: string | null;
  source_url: string | null;
  price: string | null;
  currency: string;
  planned_size: string | null;
  item: Item;
}
export interface PurchaseEdit {
  name: string | null;
  price: string | null;
  currency: string;
  planned_size: string | null;
}
type Change = { id: string } & (
  { action: 'edit'; fields: PurchaseEdit } | { action: 'passed'; reason: string } | { action: 'bought' | 'restore' }
);

export function useConsidering(state: 'considering' | 'passed') {
  const { data: session, status } = useSession();
  const client = useQueryClient();
  useSetTokenIfAvailable();
  const shelf = useQuery({
    queryKey: queryKeys.considering.list(session?.user?.email, state),
    queryFn: () => api.get<ConsideringEntry[]>('/considering', { params: { state } }),
    enabled: status !== 'loading',
    meta: { toastsOwnErrors: true },
  });
  const change = useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: (request: Change) => {
      applySessionToken(session);
      if (request.action === 'edit') return api.patch<ConsideringEntry>(`/considering/${request.id}`, request.fields);
      return api.post<ConsideringEntry>(`/considering/${request.id}/${request.action}`,
        request.action === 'passed' ? { reason: request.reason } : undefined);
    },
    onSuccess: (result) => {
      for (const target of ['considering', 'passed'] as const) {
        client.setQueryData<ConsideringEntry[]>(queryKeys.considering.list(session?.user?.email, target), (current) => {
          if (!current) return current;
          const others = current.filter((entry) => entry.item.id !== result.item.id);
          return result.state === target ? [result, ...others] : others;
        });
      }
    },
    onSettled: (_result, _error, request) => {
      client.invalidateQueries({ queryKey: queryKeys.considering.all });
      invalidateItemCaches(client, request.id);
      invalidateOutfitCaches(client);
    },
  });
  return { shelf, change };
}
