import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, getAccessToken } from '@/lib/api';
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
  useSetTokenIfAvailable();
  const queryClient = useQueryClient();
  const queryKey = ['inspiration'];
  const looks = useQuery({ queryKey, queryFn: () => api.get<InspirationLook[]>('/inspiration') });
  const create = useMutation({
    mutationFn: async (file: File) => {
      const data = new FormData();
      data.append('image', file);
      const token = getAccessToken();
      const response = await fetch('/api/v1/inspiration', {
        method: 'POST', body: data, credentials: 'include',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) throw new Error('inspiration');
      return response.json() as Promise<InspirationLook>;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
      queryClient.invalidateQueries({ queryKey: queryKeys.outfits.all });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/inspiration/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
      queryClient.invalidateQueries({ queryKey: queryKeys.outfits.all });
    },
  });
  return { looks, create, remove };
}
