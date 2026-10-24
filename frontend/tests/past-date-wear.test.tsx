import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, expect, it, vi } from 'vitest'
import OutfitDetailPage from '@/app/dashboard/outfits/[id]/page'

vi.unmock('next-intl')

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useParams: () => ({ id: 'o1' }),
}))

const outfit = vi.hoisted(() => ({
  replaces_outfit_id: null as string | null,
  id: 'o1',
  scheduled_for: null,
  source: 'on_demand',
  occasion: 'casual',
  name: 'Look' as string | null,
  items: [
    { id: 'shirt-1', name: 'Blue shirt', type: 'top', needs_wash: true },
    { id: 'trousers-2', name: 'Black trousers', type: 'bottom', needs_wash: false },
  ],
  feedback: null,
}))

vi.mock('@/lib/hooks/use-outfits', () => ({
  useOutfit: () => ({ data: outfit, isLoading: false }),
  useOutfits: () => ({ data: undefined }),
  useDeleteOutfit: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.unmock('@/lib/hooks/use-studio')
vi.mock('@/lib/api', () => ({ api: { post: vi.fn() }, setAccessToken: vi.fn() }))
vi.mock('@/lib/hooks/use-user', () => ({ useUserToday: () => () => '2026-10-05' }))
vi.mock('@/components/shared/lineage-card', () => ({ LineageCard: () => null }))
vi.mock('@/components/shared/clone-to-lookbook-dialog', () => ({
  CloneToLookbookDialog: () => null,
}))

function messages(locale: string, namespace: string) {
  return JSON.parse(
    readFileSync(resolve(__dirname, '..', 'messages', locale, `${namespace}.json`), 'utf8'),
  )
}


beforeEach(() => { vi.mocked(api.post).mockReset(); vi.mocked(api.post).mockResolvedValue({ id: 'wear' }); });
function openPicker() {
  const en = Object.fromEntries(['outfits', 'common', 'history', 'constants'].map(namespace => [namespace, messages('en', namespace)]));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><NextIntlClientProvider locale="en" messages={en}><OutfitDetailPage /></NextIntlClientProvider></QueryClientProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Wear on…' }));
  return screen.getByLabelText('Wear date');
}
it('defaults to today and submits a past date', async () => {
  const input = openPicker();
  expect(input).toHaveValue('2026-10-05');
  expect(input).toHaveAttribute('max', '2026-10-05');
  fireEvent.change(input, { target: { value: '2026-10-01' } });
  fireEvent.click(screen.getByRole('button', { name: 'Log wear' }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/outfits/o1/wear-today', { scheduled_for: '2026-10-01' }));
});
it('blocks empty and future dates and resets after cancel', () => {
  const input = openPicker();
  for (const value of ['', '2026-10-06']) {
    fireEvent.change(input, { target: { value } });
    expect(screen.getByRole('button', { name: 'Log wear' })).toBeDisabled();
  }
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  fireEvent.click(screen.getByRole('button', { name: 'Wear on…' }));
  expect(screen.getByLabelText('Wear date')).toHaveValue('2026-10-05');
  expect(api.post).not.toHaveBeenCalled();
});
