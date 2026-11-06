'use client';

import { QueryClientProvider } from '@tanstack/react-query';
import { SessionProvider } from 'next-auth/react';
import { useState } from 'react';
import { Toaster } from 'sonner';
import { ThemeProvider } from '@/components/theme-provider';
import { AuthProvider } from '@/components/auth-provider';
import { ProfileLocaleSync } from '@/components/profile-locale-sync';
import { PostHogProvider } from '@/components/posthog-provider';
import type { PostHogRuntimeConfig } from '@/lib/posthog';
import { createQueryClient } from '@/lib/query-client';

export function Providers({ children, posthog }: { children: React.ReactNode; posthog: PostHogRuntimeConfig }) {
  const [queryClient] = useState(createQueryClient);

  return (
    <SessionProvider>
      <AuthProvider>
        <QueryClientProvider client={queryClient}>
          <ProfileLocaleSync />
          {posthog.key && <PostHogProvider config={posthog} />}
          <ThemeProvider
            attribute="class"
            defaultTheme="system"
            enableSystem
            disableTransitionOnChange
          >
            {children}
            <Toaster richColors position="top-center" />
          </ThemeProvider>
        </QueryClientProvider>
      </AuthProvider>
    </SessionProvider>
  );
}
