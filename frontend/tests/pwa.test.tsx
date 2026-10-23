import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import sharp from 'sharp';
import { describe, expect, it, vi } from 'vitest';

vi.mock('next/font/google', () => ({ Inter: () => ({ variable: 'inter' }) }));
vi.mock('next-intl/server', () => ({ getLocale: async () => 'en', getMessages: async () => ({}) }));
vi.mock('next-intl', () => ({ NextIntlClientProvider: ({ children }: { children: React.ReactNode }) => children }));
vi.mock('@/app/providers', () => ({ Providers: ({ children }: { children: React.ReactNode }) => children }));

import RootLayout, { viewport } from '@/app/layout';

const publicRoot = resolve(__dirname, '../public');

describe('installable app', () => {
  it('declares standalone launch and correctly sized icons', async () => {
    const manifest = JSON.parse(readFileSync(resolve(publicRoot, 'manifest.json'), 'utf8'));
    expect(manifest).toMatchObject({ name: 'Wardrowbe', short_name: 'Wardrowbe', start_url: '/', scope: '/', display: 'standalone', theme_color: '#1a1a1a', background_color: '#1a1a1a' });
    expect(manifest.icons).toEqual(expect.arrayContaining([
      expect.objectContaining({ sizes: '192x192', purpose: 'any' }),
      expect.objectContaining({ sizes: '512x512', purpose: 'any' }),
      expect.objectContaining({ sizes: '512x512', purpose: 'maskable' }),
    ]));
    for (const icon of manifest.icons) {
      const metadata = await sharp(resolve(publicRoot, icon.src.slice(1))).metadata();
      expect(`${metadata.width}x${metadata.height}`).toBe(icon.sizes);
      expect(icon.type).toBe('image/png');
    }
  });

  it('sends credentials when the browser requests the manifest', async () => {
    const html = renderToStaticMarkup(await RootLayout({ children: <div /> }));
    const document = new DOMParser().parseFromString(html, 'text/html');
    const manifestLink = document.querySelector('link[rel="manifest"]');
    expect(manifestLink?.getAttribute('href')).toBe('/manifest.json');
    expect(manifestLink?.getAttribute('crossorigin')).toBe('use-credentials');
    expect(viewport.themeColor).toBe('#1a1a1a');
  });

  it('keeps the maskable artwork inside the central safe circle on an opaque background', async () => {
    const { data, info } = await sharp(resolve(publicRoot, 'icon-maskable-512.png')).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
    for (let row = 0; row < info.height; row++) {
      for (let column = 0; column < info.width; column++) {
        const offset = (row * info.width + column) * 4;
        if (Math.hypot(column - 255.5, row - 255.5) > 204.8) {
          expect([...data.subarray(offset, offset + 4)]).toEqual([26, 26, 26, 255]);
        }
      }
    }
  });
});
