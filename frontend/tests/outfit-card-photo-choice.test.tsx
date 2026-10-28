import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { OutfitCard } from '@/components/outfits/outfit-card';
import { OutfitPreview } from '@/components/outfit-preview';
import type { Outfit } from '@/lib/hooks/use-outfits';

vi.mock('@/lib/hooks/use-user', () => ({ useUserToday: () => () => '2026-10-28' }));
vi.mock('@/lib/hooks/use-outfit-title', () => ({ useOutfitTitle: () => () => 'Look' }));
vi.mock('@/lib/hooks/use-translated-constants', () => ({ useTypeLabel: () => (value: string) => value, useOccasionLabel: () => (value: string) => value }));
vi.mock('@/lib/hooks/use-outfit-preview', () => ({ useOutfitPreview: () => ({ preview: { image_url: '/preview.jpg' }, pending: false, draw: vi.fn(), remove: vi.fn(), like: vi.fn() }) }));

it.each(['automatic', 'photo', 'preview'] as const)('keeps pieces with the %s picture and puts the source by the title', (choice) => {
  const picture = choice === 'preview' ? '/preview.jpg' : '/photo.jpg';
  const outfit = { id: 'look', source: 'scheduled', occasion: 'casual', card_image_choice: choice, card_image_url: picture, items: [{ id: 'shirt', name: 'Blue shirt', type: 'shirt', thumbnail_url: '/shirt.jpg' }] } as Outfit;
  render(<OutfitCard outfit={outfit} />);
  expect(screen.getByAltText('Look')).toHaveAttribute('src', picture);
  expect(screen.getByAltText('Blue shirt')).toBeVisible();
  expect(screen.getByText('ai').closest('.absolute')).toBeNull();
});

it('opens preview controls from one collapsed control when a card picture is present', () => {
  render(<OutfitPreview outfitId="look" cardImageUrl="/photo.jpg"><p>Pieces</p></OutfitPreview>);
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'show' }));
  expect(screen.getByRole('img')).toBeVisible();
  for (const label of ['redraw', 'like', 'delete']) expect(screen.getByRole('button', { name: label })).toBeVisible();
});

it('does not repeat the selected preview image', () => {
  render(<OutfitPreview outfitId="look" cardImageUrl="/preview.jpg"><img src="/preview.jpg" alt="Selected picture" /></OutfitPreview>);
  expect(screen.getAllByRole('img')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'show' }));
  expect(screen.getAllByRole('img')).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'redraw' })).toBeVisible();
});

it('suppresses the chosen preview even when its signed URL was refreshed', () => {
  render(<OutfitPreview outfitId="look" cardImageUrl="/preview.jpg?signature=old" cardImageIsPreview><img src="/preview.jpg?signature=old" alt="Selected picture" /></OutfitPreview>);
  fireEvent.click(screen.getByRole('button', { name: 'show' }));
  expect(screen.getAllByRole('img')).toHaveLength(1);
});

it('keeps preview controls visible without a selected picture', () => {
  render(<OutfitPreview outfitId="look" cardImageUrl={null}><p>Pieces</p></OutfitPreview>);
  expect(screen.getByRole('img')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'show' })).not.toBeInTheDocument();
});


it('collapses the preview when the card switches from preview to photo', () => {
  const { rerender } = render(<OutfitPreview outfitId="look" cardImageUrl="/preview.jpg" cardImageIsPreview />);
  fireEvent.click(screen.getByRole('button', { name: 'show' }));
  expect(screen.getByRole('button', { name: 'redraw' })).toBeVisible();
  rerender(<OutfitPreview outfitId="look" cardImageUrl="/photo.jpg" cardImageIsPreview={false} />);
  expect(screen.getByRole('button', { name: 'show' })).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('button', { name: 'redraw' })).not.toBeInTheDocument();
});


it('keeps the AI source badge outside a real photo', () => {
  render(<OutfitCard outfit={{ id: 'look', source: 'scheduled', occasion: 'casual', card_image_url: '/photo.jpg', items: [] } as unknown as Outfit} />);
  expect(screen.getByText('ai').closest('.absolute')).toBeNull();
});
