import { getToken } from 'next-auth/jwt';
import { NextRequest, NextResponse } from 'next/server';
import { decodeProxyHeader, proxySecretMatches, publicOrigin } from '@/lib/proxy-headers';
import { sessionCookie } from '@/lib/session-cookies';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const UUID = '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}';
const PHOTO_PATHS = [
  new RegExp(`^images/${UUID}/[a-z0-9_-]+\\.(jpg|jpeg|png|webp)$`, 'i'),
  new RegExp(`^inspiration/${UUID}/image$`, 'i'),
  new RegExp(`^users/me/preview-photo/files/${UUID}/previews/[a-f0-9]{32}(?:_medium)?\\.jpg$`, 'i'),
];
const PRIVATE_HEADERS = { 'Cache-Control': 'private, no-store', Vary: 'Cookie' };

function refused(status: number) {
  return new NextResponse(null, { status, headers: PRIVATE_HEADERS });
}

export async function GET(request: NextRequest, { params }: { params: { path: string[] } }) {
  let origin: string | null;
  try { origin = publicOrigin(request.headers); } catch { return refused(400); }
  const path = params.path.join('/');
  if (request.nextUrl.search || !PHOTO_PATHS.some((pattern) => pattern.test(path))) return refused(404);

  const { name, secure } = sessionCookie();
  // Read only the cookie; getToken also accepts a caller's Authorization header.
  const session = await getToken({
    req: new NextRequest(request.url, { headers: { cookie: request.headers.get('cookie') || '' } }),
    cookieName: name,
    secureCookie: secure,
  });
  if (!session?.accessToken) return refused(401);
  const proxied = proxySecretMatches(request.headers.get('x-forward-auth-secret'));
  const remoteUser = request.headers.get('remote-user');
  if (session.proxyUser
    ? !proxied || !remoteUser || session.proxyUser !== decodeProxyHeader(remoteUser)
    : proxied) return refused(401);

  const headers = new Headers({ Authorization: `Bearer ${session.accessToken}` });
  for (const name of ['remote-user', 'remote-email', 'remote-name', 'x-forward-auth-secret']) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (origin) headers.set('host', new URL(origin).host);
  const backend = (process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || 'http://backend:8000').replace(/\/+$/, '');
  let upstream: Response;
  try {
    upstream = await fetch(`${backend}/api/v1/${path}`, {
      headers, cache: 'no-store', redirect: 'manual', signal: request.signal,
    });
  } catch { return refused(502); }
  if (!upstream.ok) {
    await upstream.body?.cancel();
    return refused(upstream.status >= 400 ? upstream.status : 502);
  }
  return new NextResponse(upstream.body, {
    status: upstream.status,
    headers: { ...PRIVATE_HEADERS, 'Content-Type': upstream.headers.get('content-type') || 'application/octet-stream' },
  });
}
