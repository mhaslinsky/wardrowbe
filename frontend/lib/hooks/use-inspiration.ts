import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { api, ApiError, API_BASE_PATH, getAccessToken } from '@/lib/api';
import { useSetTokenIfAvailable } from '@/lib/hooks/use-session-token';
import { queryKeys } from '@/lib/hooks/query-keys';

export interface InspirationLook {
  id: string;
  image_url: string;
  slots: { type: string; color: string; fit: string; pattern: string;
    item_id: string | null; match_strength: number; no_match: boolean }[];
  items: { id: string; name: string; image_url: string }[];
}

export function useInspiration() {
  const { status } = useSession();
  useSetTokenIfAvailable();
  const queryClient = useQueryClient();
  const queryKey = queryKeys.inspiration;
  const looks = useQuery({
    queryKey, queryFn: () => api.get<InspirationLook[]>('/inspiration'),
    enabled: status !== 'loading',
    meta: { toastsOwnErrors: true },
  });
  const create = useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: async (file: File) => {
      const data = new FormData();
      data.append('image', file);
      const token = getAccessToken();
      const response = await fetch(`${API_BASE_PATH}/inspiration`, {
        method: 'POST', body: data, credentials: 'include',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) {
        const body = await response.json().catch(() => {
          throw new ApiError('Invalid server response', response.status, null);
        });
        throw new ApiError(
          typeof body?.detail === 'string' ? body.detail : 'Request failed',
          response.status, body,
        );
      }
      return response.json() as Promise<InspirationLook>;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
      queryClient.invalidateQueries({ queryKey: queryKeys.outfits.all });
    },
  });
  const remove = useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: (id: string) => api.delete(`/inspiration/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
      queryClient.invalidateQueries({ queryKey: queryKeys.outfits.all });
    },
  });
  return { looks, create, remove };
}
