// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';

const originalEnv = { ...process.env };
afterEach(() => { process.env = { ...originalEnv }; vi.restoreAllMocks(); vi.resetModules(); });

it.each([',', ' , ', 'https://invalid.example, /invalid'])('rejects PUBLIC_HOSTS=%j before startup completes', async (hosts) => {
  process.env.PUBLIC_HOSTS = hosts;
  process.env.NEXT_RUNTIME = 'nodejs';
  delete process.env.AUTH_TRUST_HOST;
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(process, 'exit').mockImplementation(() => { throw new Error('startup refused'); });
  const { register } = await import('@/instrumentation');
  await expect(register()).rejects.toThrow('startup refused');
  expect(process.env.AUTH_TRUST_HOST).toBeUndefined();
});

it.each([',', ' , ', 'https://invalid.example, /invalid'])('does not enable NextAuth host trust for PUBLIC_HOSTS=%j', async (hosts) => {
  process.env.PUBLIC_HOSTS = hosts;
  delete process.env.AUTH_TRUST_HOST;
  await expect(import('@/lib/auth')).rejects.toThrow(/PUBLIC_HOSTS/);
  expect(process.env.AUTH_TRUST_HOST).toBeUndefined();
});

it('starts with PUBLIC_HOSTS unset and leaves host trust disabled', async () => {
  delete process.env.PUBLIC_HOSTS;
  delete process.env.AUTH_TRUST_HOST;
  process.env.NEXT_RUNTIME = 'nodejs';
  process.env.NEXTAUTH_SECRET = 'a-custom-secret';
  const exit = vi.spyOn(process, 'exit').mockImplementation(() => { throw new Error('unexpected exit'); });
  const { register } = await import('@/instrumentation');
  await expect(register()).resolves.toBeUndefined();
  await import('@/lib/auth');
  expect(exit).not.toHaveBeenCalled();
  expect(process.env.AUTH_TRUST_HOST).toBeUndefined();
});
