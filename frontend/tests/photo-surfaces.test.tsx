import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { OutfitCard } from '@/components/outfits/outfit-card';
import { OutfitPhoto } from '@/components/outfit-photo';
import { OutfitPreview } from '@/components/outfit-preview';
import { PreviewPhotoSettings } from '@/components/preview-photo-settings';
import { createQueryClient } from '@/lib/query-client';
import { queryKeys } from '@/lib/hooks/query-keys';
import type { Outfit } from '@/lib/types';

vi.mock('@/lib/hooks/use-user', () => ({ useUserToday: () => () => '2026-10-28' }));
vi.mock('@/lib/hooks/use-outfit-title', () => ({ useOutfitTitle: () => () => 'Look' }));
vi.mock('@/lib/hooks/use-translated-constants', () => ({ useTypeLabel: () => (value: string) => value, useOccasionLabel: () => (value: string) => value }));
vi.mock('@/lib/hooks/use-preferences', () => ({ usePreferences: () => ({ data: {} }), useUpdatePreferences: () => ({ mutate: vi.fn() }) }));

const photo = { image_url: '/photos/users/me/preview-photo/files/owner/previews/full.jpg', medium_url: '/photos/users/me/preview-photo/files/owner/previews/medium.jpg' };

it('renders session photo URLs on outfit cards, selfies, AI previews and Settings', () => {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { staleTime: Infinity, retry: false } });
  client.setQueryData(queryKeys.outfitPreview('look'), { ...photo, stale: false, created_at: '2026-10-28' });
  client.setQueryData(queryKeys.previewPhoto, { front: photo });
  const outfit = { id: 'look', source: 'manual', occasion: 'casual', items: [], selfie_photo: photo, card_image_url: photo.medium_url } as unknown as Outfit;
  const { container, unmount } = render(<QueryClientProvider client={client}>
    <OutfitCard outfit={outfit} />
    <OutfitPhoto outfit={outfit} />
    <OutfitPreview outfitId="look" />
    <PreviewPhotoSettings />
  </QueryClientProvider>);
  fireEvent.click(screen.getByRole('button', { expanded: false }));
  expect(container.querySelectorAll(`img[src="${photo.medium_url}"]`)).toHaveLength(4);
  expect(container.querySelectorAll(`a[href="${photo.image_url}"]`)).toHaveLength(3);
  unmount(); client.clear();
});
