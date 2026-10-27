// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { middleware } from '@/middleware';
import { GET as logout } from '@/app/auth/logout/route';
import { GET as proxy } from '@/app/api/v1/[...path]/route';

const HOSTS = ['wardrobe.haslinsky.net', 'christina.haslinsky.net'];
const originalEnv = { ...process.env };
const originalFetch = global.fetch;
function request(host: string, path = '/login') {
  return new NextRequest(`http://${host}${path}`, {
    headers: { host, 'x-forwarded-host': 'attacker.example', 'x-forwarded-proto': 'http' },
  });
}
beforeEach(() => {
  process.env.PUBLIC_HOSTS = HOSTS.join(',');
  process.env.NEXTAUTH_URL = `https://${HOSTS[0]}`;
  delete process.env.FORWARD_AUTH_LOGOUT_URL;
  delete process.env.TINYAUTH_URL;
  delete process.env.OIDC_END_SESSION_URL;
});
afterEach(() => { process.env = { ...originalEnv }; global.fetch = originalFetch; });

describe('public hostname routing', () => {
  it.each(HOSTS)('keeps logout on %s and uses HTTPS', async (host) => {
    const response = await logout(request(host, '/auth/logout'));
    expect(response.headers.get('location')).toBe(`https://${host}/login?loggedOut=1`);
  });
  it.each(['/login', '/dashboard', '/api/auth/session', '/asset.jpg', '/auth/logout'])('refuses an unknown host on %s', async (path) => {
    expect((await middleware(request('attacker.example', path))).status).toBe(400);
  });
  it.each(HOSTS)('replaces forwarded headers with the validated host %s', async (host) => {
    const response = await middleware(request(host));
    expect(response.headers.get('x-middleware-request-x-forwarded-host')).toBe(host);
    expect(response.headers.get('x-middleware-request-x-forwarded-proto')).toBe('https');
  });
  it('refuses unknown API hosts before contacting the backend', async () => {
    const fetchMock = vi.fn(); global.fetch = fetchMock;
    expect((await proxy(request('attacker.example', '/api/v1/users/me'))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it.each(HOSTS)('preserves validated Host across the backend hop for %s', async (host) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}')); global.fetch = fetchMock;
    await proxy(request(host, '/api/v1/health'));
    const headers = new Headers(fetchMock.mock.calls[0][1].headers);
    expect(headers.get('host')).toBe(host);
    expect(headers.get('x-forwarded-host')).toBe(host);
    expect(headers.get('x-forwarded-proto')).toBe('https');
  });
  it('leaves the localhost root health check working', async () => {
    expect((await middleware(request('localhost:3000', '/'))).status).toBe(200);
  });
  it('keeps the canonical logout fallback when the host list is unset or reset', async () => {
    delete process.env.PUBLIC_HOSTS;
    expect((await logout(request(HOSTS[1], '/auth/logout'))).headers.get('location')).toBe(`https://${HOSTS[0]}/login?loggedOut=1`);
    process.env.PUBLIC_HOSTS = '';
    expect((await logout(request(HOSTS[1], '/auth/logout'))).headers.get('location')).toBe(`https://${HOSTS[0]}/login?loggedOut=1`);
  });
  it('refuses missing and malformed public hosts instead of trusting forwarded headers', async () => {
    const missing = request(HOSTS[0]); missing.headers.delete('host');
    expect((await middleware(missing)).status).toBe(400);
    expect((await middleware(request(`${HOSTS[0]},attacker.example`))).status).toBe(400);
  });
});
