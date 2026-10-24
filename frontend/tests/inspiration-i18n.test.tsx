import { render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { NextIntlClientProvider } from 'next-intl';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import InspirationPage from '@/app/dashboard/inspiration/page';
import type { InspirationLook } from '@/lib/hooks/use-inspiration';
import { createQueryClient } from '@/lib/query-client';
import inspiration from '@/messages/en/inspiration.json';
import common from '@/messages/en/common.json';
import constants from '@/messages/en/constants.json';
// Fixtures reuse CT107 slots; all-gap grouping, items and image URLs are schema-derived.
import weakMatch from './fixtures/inspiration-weak-match.json';
import allGaps from './fixtures/inspiration-all-gaps.json';

vi.unmock('next-intl');
let client: ReturnType<typeof createQueryClient>;
beforeEach(() => {
  client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false } });
  vi.mocked(fetch).mockReset();
});
afterEach(() => client.clear());
function openPage(look: InspirationLook) {
  vi.mocked(fetch).mockImplementation(async () => new Response(JSON.stringify([look])));
  render(<NextIntlClientProvider locale="en" messages={{ inspiration, common, constants }}>
    <QueryClientProvider client={client}><InspirationPage /></QueryClientProvider>
  </NextIntlClientProvider>);
}

it('renders a weak match as an interpolated percentage', async () => {
  openPage(weakMatch);
  expect(await screen.findByText('Match: 55%')).toBeVisible();
  expect(screen.getByText('Blue jacket')).toBeVisible();
});

it('renders an all-gap saved response without matched items or percentages', async () => {
  openPage(allGaps);
  expect(await screen.findAllByText('No match')).toHaveLength(allGaps.slots.length);
  expect(screen.queryByText(/Match: \d+%/)).not.toBeInTheDocument();
  expect(screen.getByAltText('Inspiration photo')).toHaveAttribute('src', allGaps.image_url);
  expect(screen.getByRole('link', { name: 'Open outfit' })).toBeVisible();
});
