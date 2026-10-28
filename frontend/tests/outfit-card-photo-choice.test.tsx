import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { OutfitCard } from '@/components/outfits/outfit-card';
import { OutfitPreview } from '@/components/outfit-preview';
import { outfitPreviewNetworkFake } from './outfit-preview-network-fake';
import type { Outfit } from '@/lib/hooks/use-outfits';

vi.mock('@/lib/hooks/use-user', () => ({ useUserToday: () => () => '2026-10-28' }));
vi.mock('@/lib/hooks/use-outfit-preview', async () => {
  const { outfitPreviewNetworkFake } = await import('./outfit-preview-network-fake');
  return { useOutfitPreview: () => outfitPreviewNetworkFake };
});

it.each(['automatic', 'photo', 'preview'] as const)('keeps pieces with the %s picture and puts the source by the title', (choice) => {
  const picture = choice === 'preview' ? '/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg' : '/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb_medium.jpg';
  const outfit = { id: 'look', name: 'Look', source: 'scheduled', occasion: 'casual', card_image_choice: choice, card_image_url: picture, items: [{ id: 'shirt', name: 'Blue shirt', type: 'shirt', thumbnail_url: '/photos/images/11111111-1111-4111-8111-111111111111/shirt_thumb.jpg' }] } as Outfit;
  render(<OutfitCard outfit={outfit} />);
  expect(screen.getByAltText('Look')).toHaveAttribute('src', picture);
  expect(screen.getByAltText('Blue shirt')).toBeVisible();
  expect(screen.getByAltText('Look').parentElement).not.toContainElement(screen.getByText('ai'));
});

it('opens preview controls from one collapsed control when a card picture is present', () => {
  render(<OutfitPreview outfitId="look" cardImageUrl="/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb_medium.jpg" cardImageIsPreview={false}><p>Pieces</p></OutfitPreview>);
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'show' }));
  expect(screen.getByRole('img')).toBeVisible();
  for (const label of ['redraw', 'comment', 'delete']) expect(screen.getByRole('button', { name: label })).toBeVisible();
});

it('does not repeat the selected preview image', () => {
  render(<OutfitPreview outfitId="look" cardImageUrl="/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg" cardImageIsPreview><img src="/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg" alt="Selected picture" /></OutfitPreview>);
  expect(screen.getAllByRole('img')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'show' }));
  expect(screen.getAllByRole('img')).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'redraw' })).toBeVisible();
});

it('suppresses the chosen medium preview when its response has the full-size URL', () => {
  render(<OutfitPreview outfitId="look" cardImageUrl="/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_medium.jpg" cardImageIsPreview><img src="/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_medium.jpg" alt="Selected picture" /></OutfitPreview>);
  fireEvent.click(screen.getByRole('button', { name: 'show' }));
  expect(screen.getAllByRole('img')).toHaveLength(1);
});

it('keeps preview controls visible without a selected picture', () => {
  render(<OutfitPreview outfitId="look" cardImageUrl={null} cardImageIsPreview={false}><p>Pieces</p></OutfitPreview>);
  expect(screen.getByRole('img')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'show' })).not.toBeInTheDocument();
});


it('collapses the preview when the card switches from preview to photo', () => {
  const { rerender } = render(<OutfitPreview outfitId="look" cardImageUrl="/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg" cardImageIsPreview />);
  fireEvent.click(screen.getByRole('button', { name: 'show' }));
  expect(screen.getByRole('button', { name: 'redraw' })).toBeVisible();
  rerender(<OutfitPreview outfitId="look" cardImageUrl="/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb_medium.jpg" cardImageIsPreview={false} />);
  expect(screen.getByRole('button', { name: 'show' })).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('button', { name: 'redraw' })).not.toBeInTheDocument();
});


it('opens the no-preview panel and reaches creation and missing-photo guidance', () => {
  outfitPreviewNetworkFake.preview = null;
  outfitPreviewNetworkFake.missingPhoto = true;
  render(<OutfitPreview outfitId="look" cardImageUrl="/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb_medium.jpg" cardImageIsPreview={false} />);
  expect(screen.queryByRole('button', { name: 'see' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'title' }));
  fireEvent.click(screen.getByRole('button', { name: 'see' }));
  expect(outfitPreviewNetworkFake.draw).toHaveBeenCalledOnce();
  expect(screen.getByRole('link', { name: 'addPhoto' })).toBeVisible();
});

it('opens the collapsed drawing state', () => {
  outfitPreviewNetworkFake.preview = null;
  outfitPreviewNetworkFake.pending = true;
  render(<OutfitPreview outfitId="look" cardImageUrl="/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb_medium.jpg" cardImageIsPreview={false} />);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'title' }));
  expect(screen.getByRole('status')).toHaveTextContent('drawing');
  expect(screen.getByRole('button', { name: 'drawing' })).toBeDisabled();
});

it('opens the collapsed failure state and reaches retry', () => {
  outfitPreviewNetworkFake.preview = null;
  outfitPreviewNetworkFake.error = true;
  render(<OutfitPreview outfitId="look" cardImageUrl="/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb_medium.jpg" cardImageIsPreview={false} />);
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'title' }));
  expect(screen.getByRole('alert')).toHaveTextContent('failed');
  expect(screen.getByRole('button', { name: 'see' })).toBeEnabled();
});
