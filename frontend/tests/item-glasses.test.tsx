import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ItemDetailDialog } from '@/components/item-detail-dialog';
import { api } from '@/lib/api';
import { CLOTHING_TYPES, type Item } from '@/lib/types';
import { SUPPORTED_LOCALES } from '@/lib/i18n/locales';

vi.unmock('next-intl');
vi.mock('@/lib/api', () => ({
  api: { get: vi.fn().mockResolvedValue([]), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  setAccessToken: vi.fn(),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const baseItem = {
  id: 'glasses-item', type: 'glasses', status: 'ready', tags: {}, favorite: false,
  wear_count: 0, wears_since_wash: 0, effective_wash_interval: 999,
  always_in_preview: false,
} as unknown as Item;

function catalog(locale: string, namespace: string) {
  return JSON.parse(readFileSync(resolve(__dirname, '..', 'messages', locale, `${namespace}.json`), 'utf8'));
}

function openItem(item: Item) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const messages = Object.fromEntries(['wardrobe', 'constants', 'common'].map((namespace) => [namespace, catalog('en', namespace)]));
  return render(
    <QueryClientProvider client={client}>
      <NextIntlClientProvider locale="en" messages={messages} onError={() => {}}>
        <ItemDetailDialog item={item} open onOpenChange={() => {}} />
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => { vi.mocked(api.patch).mockReset().mockResolvedValue(baseItem); });
afterEach(cleanup);

it('offers glasses in the shared type picker and every locale', () => {
  expect(CLOTHING_TYPES.some((type) => type.value === 'glasses')).toBe(true);
  for (const locale of SUPPORTED_LOCALES) {
    expect(catalog(locale, 'constants').types.glasses).toBeTruthy();
    expect(catalog(locale, 'wardrobe').itemDetail.alwaysInPreview).toBeTruthy();
  }
});

it.each(['glasses', ' Glasses '])('saves the %s preview switch and reloads its saved state', async (itemType) => {
  openItem({ ...baseItem, type: itemType });
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  expect(screen.queryByText(/0 \/ 999/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Put in wash' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Edit item' }));
  const toggle = screen.getByRole('switch', { name: 'Always wear in previews' });
  expect(toggle).not.toBeChecked();
  expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
  fireEvent.click(toggle);
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/items/glasses-item', expect.objectContaining({ always_in_preview: true })));
  cleanup();
  openItem({ ...baseItem, type: itemType, always_in_preview: true });
  fireEvent.click(screen.getByRole('button', { name: 'Edit item' }));
  expect(screen.getByRole('switch', { name: 'Always wear in previews' })).toBeChecked();
  fireEvent.click(screen.getByRole('switch', { name: 'Always wear in previews' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(api.patch).toHaveBeenLastCalledWith('/items/glasses-item', expect.objectContaining({ always_in_preview: false })));
});

it('hides the switch on other types and resets an unsaved change on cancel', () => {
  openItem(baseItem);
  fireEvent.click(screen.getByRole('button', { name: 'Edit item' }));
  fireEvent.click(screen.getByRole('switch', { name: 'Always wear in previews' }));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  fireEvent.click(screen.getByRole('button', { name: 'Edit item' }));
  expect(screen.getByRole('switch', { name: 'Always wear in previews' })).not.toBeChecked();
  cleanup();
  openItem({ ...baseItem, type: 'shirt' });
  fireEvent.click(screen.getByRole('button', { name: 'Edit item' }));
  expect(screen.queryByRole('switch', { name: 'Always wear in previews' })).not.toBeInTheDocument();
  expect(screen.getByRole('spinbutton')).toBeInTheDocument();
});
