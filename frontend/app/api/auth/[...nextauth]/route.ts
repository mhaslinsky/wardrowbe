import NextAuth from 'next-auth';
import { NextRequest, NextResponse } from 'next/server';
import { authOptions } from '@/lib/auth';
import { publicOrigin } from '@/lib/proxy-headers';
import { safeCallbackUrl } from '@/lib/callback-url';

type Context = { params: { nextauth: string[] } };

async function handler(request: NextRequest, context: Context) {
  let origin: string | null;
  try { origin = publicOrigin(request.headers); } catch {
    return NextResponse.json({ detail: 'Unknown host' }, { status: 400 });
  }
  if (!origin) return NextAuth(authOptions)(request, context);
  return NextAuth({
    ...authOptions,
    useSecureCookies: true,
    callbacks: {
      ...authOptions.callbacks,
      redirect: async ({ url }) => `${origin}${safeCallbackUrl(url, origin)}`,
    },
  })(request, context);
}

export { handler as GET, handler as POST };
