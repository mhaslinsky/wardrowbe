'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { z } from 'zod';
import { api } from '@/lib/api';
import { applySessionToken, useSetTokenIfAvailable } from '@/lib/hooks/use-session-token';

const text = z.string();
const measurement = z.number().finite().positive().max(300).nullable();
const profileSchema = z.object({
  rev: text.min(1).max(64),
  brand_sizes: z.array(z.object({ brand: text, line: text, size: text, note: text })),
  skip_list: z.array(z.object({ brand: text, line: text, reason: text })),
  neck: measurement,
  sleeve: measurement,
  thigh: measurement,
  reference_garment: text,
});
export type FitProfile = z.infer<typeof profileSchema>;
const endpoint = '/users/me/preferences/fit-profile';
const profileKey = (email?: string | null) => ['fit-profile', email] as const;

export function useFitProfile() {
  const { data: session, status } = useSession();
  useSetTokenIfAvailable();
  return useQuery({
    queryKey: profileKey(session?.user?.email),
    queryFn: async () => profileSchema.parse(await api.get<unknown>(endpoint)),
    enabled: status !== 'loading',
    retry: false,
  });
}

export function useUpdateFitProfile() {
  const client = useQueryClient();
  const { data: session } = useSession();
  return useMutation({
    mutationFn: async (profile: FitProfile) => {
      applySessionToken(session);
      return profileSchema.parse(await api.patch<unknown>(endpoint, profile));
    },
    onSuccess: (saved) => client.setQueryData(profileKey(session?.user?.email), saved),
  });
}
