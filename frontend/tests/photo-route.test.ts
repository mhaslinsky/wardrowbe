// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { encode } from 'next-auth/jwt';
import type { JWT } from 'next-auth/jwt';
import { NextRequest } from 'next/server';
import { GET } from '@/app/photos/[...path]/route';

const owner = '11111111-1111-4111-8111-111111111111';
const paths = [`images/${owner}/photo.jpg`, `inspiration/${owner}/image`, `users/me/preview-photo/files/${owner}/previews/${'a'.repeat(32)}.jpg`];
const secret = 'disposable-photo-route-session-secret';
const originalEnv = { ...process.env };

beforeEach(() => {
  process.env.NEXTAUTH_SECRET = secret;
  process.env.NEXTAUTH_URL = 'http://localhost:3000';
  process.env.BACKEND_URL = 'http://backend:8000/';
  delete process.env.PUBLIC_HOSTS;
  delete process.env.FORWARD_AUTH_SECRET;
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => new Response(new Uint8Array([1, 2, 3]), {
    headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'public, max-age=86400', 'Set-Cookie': 'upstream=bad' },
  })));
});
afterEach(() => { process.env = { ...originalEnv }; vi.unstubAllGlobals(); });

async function request(path = paths[0], token: JWT | null = { accessToken: 'owner-token' }, extra: Record<string, string> = {}, maxAge = 3600) {
  const secure = Boolean(process.env.PUBLIC_HOSTS);
  const value = token ? await encode({ secret, token, maxAge }) : '';
  const name = `${secure ? '__Secure-' : ''}next-auth.session-token`;
  const headers = { cookie: `${name}.0=${value.slice(0, value.length / 2)}; ${name}.1=${value.slice(value.length / 2)}`, ...extra };
  return GET(new NextRequest(`http://localhost:3000/photos/${path}`, { headers }), { params: { path: path.split('?')[0].split('/') } });
}

it.each(paths)('streams owner bytes for %s using only the session backend token', async (path) => {
  const response = await request(path, { accessToken: 'owner-token' }, { authorization: 'Bearer supplied-token' });
  expect(response.status).toBe(200);
  expect(Array.from(new Uint8Array(await response.arrayBuffer()))).toEqual([1, 2, 3]);
  expect(response.headers.get('cache-control')).toBe('private, no-store');
  expect(response.headers.get('content-type')).toBe('image/jpeg');
  expect(response.headers.get('set-cookie')).toBeNull();
  const [url, options] = vi.mocked(fetch).mock.calls[0];
  expect(url).toBe(`http://backend:8000/api/v1/${path}`);
  expect(options).toMatchObject({ cache: 'no-store', redirect: 'manual' });
  const headers = options?.headers as Headers;
  expect(headers.get('authorization')).toBe('Bearer owner-token');
  expect(headers.get('cookie')).toBeNull();
});

it.each([null, {}, { accessToken: '' }])('refuses a missing session backend token %j', async (token) => {
  expect((await request(paths[0], token)).status).toBe(401);
  expect(fetch).not.toHaveBeenCalled();
});

it('refuses an expired, malformed or absent cookie even with a supplied bearer token', async () => {
  expect((await request(paths[0], { accessToken: 'expired' }, {}, -60)).status).toBe(401);
  const bearer = await encode({ secret, token: { accessToken: 'injected-token' } });
  for (const cookie of ['', 'next-auth.session-token=invalid']) {
    expect((await request(paths[0], null, { cookie, authorization: `Bearer ${bearer}` })).status).toBe(401);
  }
  expect(fetch).not.toHaveBeenCalled();
});

it('uses the requesting session when another user copies the same URL', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(new Response(null, { status: 401 }));
  const response = await request(paths[0], { accessToken: 'other-token' });
  expect(response.status).toBe(401);
  expect((vi.mocked(fetch).mock.calls[0][1]?.headers as Headers).get('authorization')).toBe('Bearer other-token');
  expect(response.headers.get('cache-control')).toBe('private, no-store');
});

it('reads secure chunked cookies and preserves the validated proxy identity', async () => {
  process.env.PUBLIC_HOSTS = 'wardrobe.example.com';
  process.env.FORWARD_AUTH_SECRET = 'local-proxy-secret';
  const response = await request(paths[0], { accessToken: 'owner-token', proxyUser: 'owner' }, {
    host: 'wardrobe.example.com', 'remote-user': 'owner', 'x-forward-auth-secret': 'local-proxy-secret',
  });
  expect(response.status).toBe(200);
  const headers = vi.mocked(fetch).mock.calls[0][1]?.headers as Headers;
  expect(headers.get('host')).toBe('wardrobe.example.com');
  expect(headers.get('remote-user')).toBe('owner');
  expect(headers.get('x-forward-auth-secret')).toBe('local-proxy-secret');
});

it('refuses a switched or missing proxy identity and a non-proxy session at the proxy', async () => {
  process.env.FORWARD_AUTH_SECRET = 'local-proxy-secret';
  for (const remote of [undefined, 'other']) {
    const headers: Record<string, string> = { 'x-forward-auth-secret': 'local-proxy-secret' };
    if (remote) headers['remote-user'] = remote;
    expect((await request(paths[0], { accessToken: 'owner', proxyUser: 'owner' }, headers)).status).toBe(401);
  }
  expect((await request(paths[0], { accessToken: 'owner', proxyUser: 'owner' })).status).toBe(401);
  expect((await request(paths[0], { accessToken: 'owner' }, { 'x-forward-auth-secret': 'local-proxy-secret' })).status).toBe(401);
  expect(fetch).not.toHaveBeenCalled();
});

it.each(['auth/session', 'https://evil.example/a', `images/${owner}/../private.jpg`, `images/${owner}/a%2fb.jpg`, `${paths[0]}?sig=old&expires=9999999999`, ''])('rejects non-photo path %s', async (path) => {
  expect((await request(path)).status).toBe(404);
  expect(fetch).not.toHaveBeenCalled();
});

it.each([302, 401, 403, 404, 500])('keeps upstream failure %s private and never follows redirects', async (status) => {
  vi.mocked(fetch).mockResolvedValue(new Response('upstream details', { status, headers: { Location: 'https://elsewhere', 'Cache-Control': 'public' } }));
  const response = await request();
  expect(response.status).toBe(status < 400 ? 502 : status);
  expect(response.headers.get('cache-control')).toBe('private, no-store');
  expect(response.headers.get('location')).toBeNull();
  expect(await response.text()).toBe('');
});

it('returns a private failure on network error and rejects an unknown host', async () => {
  vi.mocked(fetch).mockRejectedValue(new Error('offline'));
  expect((await request()).status).toBe(502);
  process.env.PUBLIC_HOSTS = 'wardrobe.example.com';
  expect((await request()).status).toBe(400);
});
