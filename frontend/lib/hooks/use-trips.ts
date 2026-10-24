'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { queryKeys } from '@/lib/hooks/query-keys';
import { api } from '@/lib/api';
import { invalidateOutfitCaches } from '@/lib/hooks/cache-invalidation';
import { applySessionToken, useSetTokenIfAvailable } from '@/lib/hooks/use-session-token';

export interface TripRequest {
  destination: string;
  start_date: string;
  end_date: string;
  occasions: string[];
}
export interface Trip extends TripRequest {
  id: string;
  days: { date: string; outfit_id: string; weather_source: 'forecast' | 'climate'; low: number; high: number }[];
  packing: { item_id: string; name: string | null; count: number; days_worn: number }[];
}

export function useTrips() {
  const { data: session, status } = useSession();
  const client = useQueryClient();
  useSetTokenIfAvailable();
  const trips = useQuery({
    queryKey: queryKeys.trips.list(session?.user?.email),
    queryFn: () => api.get<Trip[]>('/trips'),
    enabled: status !== 'loading',
    meta: { toastsOwnErrors: true },
  });
  const refresh = () => {
    client.invalidateQueries({ queryKey: queryKeys.trips.all });
    invalidateOutfitCaches(client);
  };
  const plan = useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: (request: TripRequest) => {
      applySessionToken(session);
      return api.post<Trip>('/trips', request);
    },
    onSuccess: refresh,
  });
  const remove = useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: (id: string) => {
      applySessionToken(session);
      return api.delete<void>(`/trips/${id}`);
    },
    onSuccess: refresh,
  });
  return { trips, plan, remove };
}
