// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { createRequire } from 'node:module';
import { AsyncLocalStorage } from 'node:async_hooks';

Object.assign(globalThis, { AsyncLocalStorage });
const context = createRequire(import.meta.url)('next/dist/client/components/request-async-storage.external').requestAsyncStorage as AsyncLocalStorage<{ headers: Headers; cookies: { getAll: () => [] } }>;
const originalEnv = { ...process.env };
const HOSTS = ['wardrobe.haslinsky.net', 'christina.haslinsky.net'];
beforeEach(() => {
  vi.resetModules();
  process.env.PUBLIC_HOSTS = HOSTS.join(',');
  process.env.NEXTAUTH_URL = `https://${HOSTS[0]}`;
  process.env.NEXTAUTH_SECRET = 'test-nextauth-secret';
  delete process.env.AUTH_TRUST_HOST;
});
afterEach(() => { process.env = { ...originalEnv }; });

it('uses actual NextAuth provider endpoints on each request host concurrently', async () => {
  const { GET } = await import('@/app/api/auth/[...nextauth]/route');
  const { middleware } = await import('@/middleware');
  const canonical = process.env.NEXTAUTH_URL;
  const responses = await Promise.all(HOSTS.map(async (host) => {
    const request = new NextRequest(`http://${host}/api/auth/providers`, {
      headers: { host, 'x-forwarded-host': 'attacker.example', 'x-forwarded-proto': 'http' },
    });
    const checked = await middleware(request);
    const headers = new Headers(request.headers);
    headers.set('x-forwarded-host', checked.headers.get('x-middleware-request-x-forwarded-host')!);
    headers.set('x-forwarded-proto', checked.headers.get('x-middleware-request-x-forwarded-proto')!);
    return context.run({ headers, cookies: { getAll: () => [] } }, () => GET(request, { params: { nextauth: ['providers'] } }));
  }));
  for (const index of [0, 1]) {
    const response = responses[index];
    expect(response.status).toBe(200);
    const providers = await response.json();
    expect(providers['forward-auth'].signinUrl).toBe(`https://${HOSTS[index]}/api/auth/signin/forward-auth`);
    expect(providers['forward-auth'].callbackUrl).toBe(`https://${HOSTS[index]}/api/auth/callback/forward-auth`);
  }
  expect(process.env.NEXTAUTH_URL).toBe(canonical);
});

it('returns secure host-only NextAuth cookies for each hostname', async () => {
  const { GET } = await import('@/app/api/auth/[...nextauth]/route');
  for (const host of HOSTS) {
    const headers = new Headers({ host, 'x-forwarded-host': host, 'x-forwarded-proto': 'https' });
    const request = new NextRequest(`https://${host}/api/auth/csrf`, { headers });
    const response = await context.run({ headers, cookies: { getAll: () => [] } }, () => GET(request, { params: { nextauth: ['csrf'] } }));
    expect(response.status).toBe(200);
    const cookies = response.headers.getSetCookie();
    expect(cookies.length).toBeGreaterThan(0);
    for (const cookie of cookies) {
      expect(cookie).toMatch(/; Secure/i);
      expect(cookie).not.toMatch(/; Domain=/i);
    }
  }
});
