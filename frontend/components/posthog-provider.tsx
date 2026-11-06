'use client';

import { useEffect, useState } from 'react';
import type { PostHog } from 'posthog-js';
import { useAuth } from '@/lib/hooks/use-auth';
import { initializePostHog, type PostHogRuntimeConfig } from '@/lib/posthog';

export function PostHogProvider({ config }: { config: PostHogRuntimeConfig }) {
  const [client, setClient] = useState<PostHog>();
  const { user, sessionStatus: status } = useAuth();

  useEffect(() => {
    let active = true;
    void initializePostHog(config, window.location.hostname).then((loaded) => {
      if (active) setClient(loaded);
    }).catch((error) => console.error('PostHog failed to load', error));
    return () => { active = false; };
  }, [config]);

  useEffect(() => {
    if (status === 'unauthenticated') client?.reset();
    else if (status === 'authenticated' && user?.id) client?.identify(user.id);
  }, [client, status, user?.id]);

  return null;
}
