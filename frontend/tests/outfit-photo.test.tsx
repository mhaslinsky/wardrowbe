import { render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { OutfitCard } from '@/components/outfits/outfit-card'
import type { Outfit } from '@/lib/hooks/use-outfits'

vi.mock('@/lib/hooks/use-user', () => ({ useUserToday: () => () => '2026-10-24' }))
vi.mock('@/lib/hooks/use-outfit-title', () => ({ useOutfitTitle: () => () => 'Look' }))
vi.mock('@/lib/hooks/use-translated-constants', () => ({ useTypeLabel: () => (value: string) => value, useOccasionLabel: () => (value: string) => value }))

const outfit = (count: number) => ({
  id: 'look', source: 'manual', occasion: 'casual',
  items: Array.from({ length: count }, (_, index) => ({ id: `piece-${index}`, type: 'shirt', needs_wash: true })),
}) as Outfit


it('shows the selected outfit photo on the card', () => {
  const { container } = render(<OutfitCard outfit={{ ...outfit(1), card_image_url: '/private-photo.jpg' }} />);
  expect(container.querySelector('img')).toHaveAttribute('src', '/private-photo.jpg');
});

import { fireEvent, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { createQueryClient } from '@/lib/query-client';
import { OutfitPhoto } from '@/components/outfit-photo';
import { queryKeys } from '@/lib/hooks/query-keys';

it('attaches, chooses, deletes and reports failures', async () => {
  vi.mocked(fetch).mockResolvedValue({ ok: true, status: 200, json: async () => ({}) } as Response);
  const client = createQueryClient();
  client.setQueryData(queryKeys.outfit('look'), { before: true });
  render(<QueryClientProvider client={client}><OutfitPhoto outfit={{ ...outfit(1), selfie_photo: { image_url: '/me.jpg', medium_url: '/small.jpg' }, card_image_choice: 'automatic' }} /></QueryClientProvider>);
  expect(screen.getByAltText('title')).toHaveAttribute('src', '/small.jpg');
  fireEvent.change(screen.getByLabelText('add'), { target: { files: [new File(['photo'], 'me.jpg', { type: 'image/jpeg' })] } });
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/look/photo'), expect.objectContaining({ method: 'PUT', body: expect.any(FormData) })));
  await waitFor(() => expect(screen.getByLabelText('choice')).toBeEnabled());
  expect(client.getQueryState(queryKeys.outfit('look'))?.isInvalidated).toBe(true);
  fireEvent.change(screen.getByLabelText('choice'), { target: { value: 'preview' } });
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/photo-choice'), expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ choice: 'preview' }) })));
  await waitFor(() => expect(screen.getByRole('button', { name: 'remove' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'remove' }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/look/photo'), expect.objectContaining({ method: 'DELETE' })));
  await waitFor(() => expect(screen.getByLabelText('choice')).toBeEnabled());
  vi.mocked(fetch).mockResolvedValue({ ok: false, status: 400, json: async () => ({}) } as Response);
  fireEvent.change(screen.getByLabelText('choice'), { target: { value: 'photo' } });
  expect(await screen.findByRole('alert')).toHaveTextContent('failed');
});

import { act, renderHook } from '@testing-library/react';
import { useOutfitPreview } from '@/lib/hooks/use-outfit-preview';

it.each(['draw', 'remove'] as const)('refreshes outfit cards after preview %s', async (action) => {
  const client = createQueryClient();
  client.setQueryData(queryKeys.outfit('look'), { before: true });
  client.setQueryData(queryKeys.outfits.all, { before: true });
  vi.mocked(fetch).mockResolvedValue({ ok: true, status: 200, json: async () => ({ image_url: '/ai.jpg', created_at: '2026-10-27T00:00:00Z' }) } as Response);
  const { result } = renderHook(() => useOutfitPreview('look'), { wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> });
  await waitFor(() => expect(result.current.preview).toBeDefined());
  await act(async () => { if (action === 'draw') await result.current.draw(true); else await result.current.remove(); });
  expect(client.getQueryState(queryKeys.outfit('look'))?.isInvalidated).toBe(true);
  expect(client.getQueryState(queryKeys.outfits.all)?.isInvalidated).toBe(true);
});
