import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { afterEach, expect, it, vi } from 'vitest';
import { ItemPhotoViewer } from '@/components/item-photo-viewer';

vi.unmock('next-intl');
afterEach(cleanup);
it.each(['en', 'de', 'fr', 'it', 'ja', 'ko', 'zh-CN', 'zh-TW'])('localizes viewer labels in %s', (locale) => {
  const load = (namespace: string) => JSON.parse(readFileSync(resolve(__dirname, '../messages', locale, `${namespace}.json`), 'utf8'));
  const wardrobe = load('wardrobe');
  const common = load('common');
  const labels = wardrobe.photoViewer;
  render(<NextIntlClientProvider locale={locale} messages={{ wardrobe, common }}>
    <ItemPhotoViewer images={[{ id: 'primary', url: '/full.jpg' }]} index={0} alt="Shirt">Photo</ItemPhotoViewer>
  </NextIntlClientProvider>);
  fireEvent.click(screen.getByLabelText(labels.open));
  expect(screen.getByRole('dialog', { name: labels.lightbox })).toBeInTheDocument();
  expect(screen.getByRole('region', { name: labels.gallery })).toHaveAttribute('aria-roledescription', labels.carousel);
  expect(screen.getByRole('group', { name: labels.slideCount.replace('{index}', '1').replace('{total}', '1') })).toHaveAttribute('aria-roledescription', labels.slide);
  for (const label of [common.close, common.previous, common.next, labels.actualSize]) expect(screen.getByLabelText(label)).toBeInTheDocument();
  for (const key of ['close', 'previous', 'next']) expect(labels).not.toHaveProperty(key);
});
