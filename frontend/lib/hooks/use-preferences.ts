'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { api } from '@/lib/api';
import { useSetTokenIfAvailable, applySessionToken } from '@/lib/hooks/use-session-token';
import { Preferences } from '@/lib/types';
import { queryKeys } from '@/lib/hooks/query-keys';

export function usePreferences() {
  const { status } = useSession();
  useSetTokenIfAvailable();

  return useQuery({
    queryKey: queryKeys.preferences,
    queryFn: () => api.get<Preferences>('/users/me/preferences'),
    enabled: status !== 'loading',
  });
}

export function useUpdatePreferences({ toastsOwnErrors = false } = {}) {
  const queryClient = useQueryClient();
  const { data: session } = useSession();

  return useMutation({
    meta: { toastsOwnErrors },
    mutationFn: (data: Partial<Preferences>) => {
      applySessionToken(session);
      return api.patch<Preferences>('/users/me/preferences', data);
    },
    onSuccess: (saved, submitted) => {
      const changed = Object.fromEntries(
        Object.keys(submitted).map((key) => [key, saved[key as keyof Preferences]]),
      );
      queryClient.setQueryData<Preferences>(queryKeys.preferences, (previous) =>
        previous ? { ...previous, ...changed } : saved,
      );
      queryClient.invalidateQueries({ queryKey: queryKeys.preferences });
      queryClient.invalidateQueries({ queryKey: queryKeys.items.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.everyItem });
      queryClient.invalidateQueries({ queryKey: queryKeys.outfitPreviews });
    },
  });
}

export function useResetPreferences({ toastsOwnErrors = false } = {}) {
  const queryClient = useQueryClient();
  const { data: session } = useSession();

  return useMutation({
    meta: { toastsOwnErrors },
    mutationFn: (rev: string) => {
      applySessionToken(session);
      return api.post<Preferences>('/users/me/preferences/reset', { rev });
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(queryKeys.preferences, saved);
      queryClient.invalidateQueries({ queryKey: queryKeys.preferences });
      queryClient.invalidateQueries({ queryKey: queryKeys.items.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.everyItem });
      queryClient.invalidateQueries({ queryKey: queryKeys.outfitPreviews });
    },
  });
}

interface AITestResult {
  status: 'connected' | 'error';
  available_models?: string[];
  vision_models?: string[];
  text_models?: string[];
  error?: string;
}

export function useTestAIEndpoint() {
  const { data: session } = useSession();

  return useMutation({
    mutationFn: (url: string) => {
      applySessionToken(session);
      return api.post<AITestResult>('/users/me/preferences/test-ai-endpoint', { url });
    },
  });
}
