'use client';

import { useMutation, useQueries } from '@tanstack/react-query';
import { z } from 'zod';
import { useSession } from 'next-auth/react';
import type { Item } from '@/lib/types';
import { api } from '@/lib/api';
import { queryKeys } from './query-keys';
import { applySessionToken } from './use-session-token';

const citationSchema = z.object({
  source_id: z.string(), item_id: z.string().nullish(), profile_rev: z.string().nullish(),
  row_key: z.string().nullish(), field: z.string(), quote: z.string().nullish(), value: z.number().nullish(),
});
const reasonSchema = z.object({
  kind: z.enum(['fit_and_cut', 'feel_and_quality', 'style_and_look', 'few_outfits', 'skip', 'wrong_size', 'duplicate']),
  citations: z.array(citationSchema).min(1),
});
const resultSchema = z.object({
  status: z.enum(['scored', 'insufficient', 'skip', 'wrong_size', 'duplicate', 'error']),
  percentage: z.number().int().min(0).max(100).nullable(),
  pluses: z.array(reasonSchema), minuses: z.array(reasonSchema), unknowns: z.array(reasonSchema),
  near_duplicate: z.boolean(), error: z.enum(['missing_key', 'disabled', 'unavailable']).nullish(),
  outfits: z.object({
    status: z.enum(['known', 'unknown', 'not_applicable']), count: z.number().int().nonnegative().optional(),
    ideas: z.array(z.object({ item_ids: z.array(z.string()) })).optional(),
    pool_truncated: z.boolean().optional(), idea_limit_truncated: z.boolean().optional(),
  }),
}).refine(result => result.status !== 'scored' || result.percentage !== null)
  .refine(result => result.outfits.status !== 'known' || (result.outfits.count !== undefined && result.outfits.ideas !== undefined));
export type BuyCheckReason = z.infer<typeof reasonSchema>;
export type BuyCheckResult = z.infer<typeof resultSchema>;

export function useConsideringBuyCheck(itemId: string) {
  const { data: session } = useSession();
  return useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: async (signal: AbortSignal) => {
      applySessionToken(session);
      return resultSchema.parse(await api.post<unknown>(`/considering/${itemId}/buy-check`, {}, { signal }));
    },
  });
}

export function useBuyCheckItems(result: BuyCheckResult | undefined, itemId: string) {
  const { data: session } = useSession();
  const ids = Array.from(new Set([
    ...(result ? [...result.pluses, ...result.minuses, ...result.unknowns].flatMap(reason => reason.citations.flatMap(citation => citation.item_id ? [citation.item_id] : [])) : []),
    ...(result?.outfits.ideas?.flatMap(idea => idea.item_ids) ?? []),
  ])).filter(id => id !== itemId);
  const items = useQueries({ queries: ids.map(id => ({
    queryKey: queryKeys.item(id),
    queryFn: () => { applySessionToken(session); return api.get<Item>(`/items/${encodeURIComponent(id)}`); },
    meta: { toastsOwnErrors: true },
  })) });
  return new Map(ids.map((id, index) => [id, items[index].data?.name]));
}
