import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { OutfitHistoryCard } from '@/components/outfit-history-card';
import OutfitDetailPage from '@/app/dashboard/outfits/[id]/page';
import type { Outfit } from '@/lib/hooks/use-outfits';

const fixture = vi.hoisted(() => ({
  id: 'look', source: 'manual', occasion: 'casual', status: 'accepted', name: 'Look',
  card_image_choice: 'photo', card_image_url: '/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb_medium.jpg' as string | null,
  selfie_photo: { image_url: '/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg' },
  items: [{ id: 'shirt', type: 'shirt', name: 'Blue shirt', thumbnail_url: '/photos/images/11111111-1111-4111-8111-111111111111/shirt_thumb.jpg' }],
}));
vi.mock('next/navigation', () => ({ useParams: () => ({ id: 'look' }), useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/lib/hooks/use-outfits', () => ({
  useOutfit: () => ({ data: fixture, isLoading: false }),
  useOutfits: () => ({ data: undefined }),
  useDeleteOutfit: () => ({ mutateAsync: vi.fn() }),
  useRejectOutfit: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock('@/lib/hooks/use-studio', () => ({ useWearToday: () => ({ mutateAsync: vi.fn() }) }));
vi.mock('@/lib/hooks/use-user', () => ({ useUserToday: () => () => '2026-10-28' }));
vi.mock('@/lib/hooks/use-outfit-preview', async () => {
  const { outfitPreviewNetworkFake } = await import('./outfit-preview-network-fake');
  return { useOutfitPreview: () => outfitPreviewNetworkFake };
});
vi.mock('@/components/shared/lineage-card', () => ({ LineageCard: () => null }));
vi.mock('@/components/shared/clone-to-lookbook-dialog', () => ({ CloneToLookbookDialog: () => null }));
vi.mock('@/components/outfit-photo', () => ({ OutfitPhoto: () => <p>Photo controls</p> }));

it('history keeps its selected picture and pieces with reachable preview controls', () => {
  render(<OutfitHistoryCard outfit={fixture as Outfit} onFeedback={vi.fn()} />);
  expect(screen.getByAltText('outfitPicture')).toHaveAttribute('src', '/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb_medium.jpg');
  expect(screen.getByAltText('Blue shirt')).toBeVisible();
  expect(screen.queryByRole('img', { name: 'see' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'show' }));
  expect(screen.getByRole('button', { name: 'redraw' })).toBeVisible();
});

it('detail shows the selected picture and pieces with a collapsed preview', () => {
  render(<OutfitDetailPage />);
  expect(screen.getByAltText('Look')).toHaveAttribute('src', '/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb_medium.jpg');
  expect(screen.getByText('Blue shirt')).toBeVisible();
  expect(screen.queryByRole('img', { name: 'see' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'show' }));
  expect(screen.getByRole('button', { name: 'comment' })).toBeVisible();
});

it('detail keeps photo controls inline when no card picture is selected', () => {
  fixture.card_image_url = null;
  try {
    render(<OutfitDetailPage />);
    expect(screen.getByText('Photo controls')).toBeVisible();
    expect(screen.getByText('Photo controls').closest('details')).toBeNull();
  } finally {
    fixture.card_image_url = '/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb_medium.jpg';
  }
});
