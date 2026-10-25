import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ItemDetailDialog } from '@/components/item-detail-dialog';
import type { Item } from '@/lib/types';

const mutations = vi.hoisted(() => ({ deleteImage: vi.fn(), setPrimary: vi.fn() }));
vi.mock('next/image', () => ({ default: ({ fill, ...props }: any) => <img {...props} /> }));
vi.mock('@/components/generate-pairings-dialog', () => ({ GeneratePairingsDialog: () => null }));
vi.mock('@/lib/hooks/use-features', () => ({ useFeatures: () => ({ data: {} }) }));
vi.mock('@/lib/hooks/use-items', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/lib/hooks/use-items')>();
  return Object.fromEntries(Object.keys(original).map((key) => [key,
    () => ({ data: undefined, isPending: false, mutateAsync: vi.fn(), mutate: key === 'useDeleteItemImage' ? mutations.deleteImage : mutations.setPrimary }),
  ]));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const item = {
  id: 'shirt', name: 'Shirt', type: 'shirt', status: 'ready', tags: {},
  image_url: '/full.jpg?signature=unchanged', medium_url: '/medium.webp', thumbnail_url: '/thumb.webp',
  additional_images: [{ id: 'extra', image_url: '/extra.webp' }],
} as Item;

it.each(['close', 'escape', 'backdrop'])('opens the gallery, cycles full sources, and closes via %s', async (method) => {
  const onOpenChange = vi.fn();
  render(<QueryClientProvider client={new QueryClient()}>
    <ItemDetailDialog item={item} open onOpenChange={onOpenChange} />
  </QueryClientProvider>);
  const trigger = screen.getByLabelText('open', { exact: true });
  fireEvent.click(trigger);
  const current = () => document.querySelector('.yarl__slide_current img');
  await waitFor(() => expect(current()).toHaveAttribute('src', item.image_url));
  expect(current()).not.toHaveAttribute('srcset');
  fireEvent.click(screen.getByLabelText('next', { exact: true }));
  await waitFor(() => expect(current()).toHaveAttribute('src', '/extra.webp'));
  const container = document.querySelector('.yarl__container')!;
  fireEvent.keyDown(container, { key: 'ArrowRight' });
  await waitFor(() => expect(current()).toHaveAttribute('src', item.image_url));
  if (method === 'close') fireEvent.click(screen.getByLabelText('close', { exact: true }));
  if (method === 'escape') fireEvent.keyDown(container, { key: 'Escape' });
  if (method === 'backdrop') {
    const backdrop = document.querySelector('.yarl__slide_current')!;
    fireEvent.pointerDown(backdrop, { pointerId: 1 });
    fireEvent.pointerUp(backdrop, { pointerId: 1 });
  }
  await waitFor(() => expect(document.querySelector('.yarl__root')).toBeNull());
  expect(onOpenChange).not.toHaveBeenCalled();
  expect(trigger).toHaveFocus();
});

it.each([['redrawn', 'original'], ['original', 'redrawn']] as const)(
  'shows a %s item with a labelled %s comparison without writes', async (selected, alternate) => {
    const request = vi.fn();
    vi.stubGlobal('fetch', request);
    const comparisonItem = { ...item, photo_redraw: { selected },
      photo_choices: { original: '/original.jpg', redrawn: '/redrawn.jpg' } } as Item;
    render(<QueryClientProvider client={new QueryClient()}>
      <ItemDetailDialog item={comparisonItem} open onOpenChange={vi.fn()} />
    </QueryClientProvider>);
    fireEvent.click(screen.getByRole('button', { name: `photoViewer.${alternate}`, exact: true }));
    fireEvent.click(screen.getByLabelText('open', { exact: true }));
    await waitFor(() => expect(document.querySelector('.yarl__slide_current img'))
      .toHaveAttribute('src', comparisonItem.photo_choices![alternate]));
    expect(document.querySelector('.yarl__slide_current [data-testid=photo-version-label]')).toHaveTextContent(alternate);
    expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('alt', 'versionAlt');
    expect(request).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText('next', { exact: true }));
    await waitFor(() => expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', '/extra.webp'));
  });

it.each([undefined, {}, { original: '/original.jpg' }, { redrawn: '/redrawn.jpg' }])(
  'keeps single-version and missing choices unchanged: %j', (choices) => {
    render(<QueryClientProvider client={new QueryClient()}>
      <ItemDetailDialog item={{ ...item, additional_images: [], photo_choices: choices } as Item} open onOpenChange={vi.fn()} />
    </QueryClientProvider>);
    expect(screen.queryByRole('button', { name: 'photoViewer.original', exact: true })).toBeNull();
    expect(screen.queryByRole('button', { name: 'photoViewer.redrawn', exact: true })).toBeNull();
  });

const customItem = { ...item, photo_redraw: { selected: 'custom' },
  photo_choices: { original: '/original.jpg', redrawn: '/redrawn.jpg' } } as Item;

it('shows both stored versions after a custom primary photo', async () => {
  render(<QueryClientProvider client={new QueryClient()}>
    <ItemDetailDialog item={customItem} open onOpenChange={vi.fn()} />
  </QueryClientProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'photoViewer.original', exact: true }));
  fireEvent.click(screen.getByLabelText('open', { exact: true }));
  await waitFor(() => expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', '/original.jpg'));
  fireEvent.click(screen.getByLabelText('next', { exact: true }));
  await waitFor(() => expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', '/redrawn.jpg'));
});

it.each(['titles.deleteImage', 'titles.setAsPrimary'])('keeps extra-photo actions correct with comparison slides: %s', async (action) => {
  render(<QueryClientProvider client={new QueryClient()}>
    <ItemDetailDialog item={customItem} open onOpenChange={vi.fn()} />
  </QueryClientProvider>);
  fireEvent.click(screen.getByTitle('actions.editItem'));
  const extra = document.querySelector('button img[src="/extra.webp"]')!;
  fireEvent.click(extra.closest('button')!);
  fireEvent.click(screen.getByTitle(action));
  const mutation = action === 'titles.deleteImage' ? mutations.deleteImage : mutations.setPrimary;
  expect(mutation).toHaveBeenCalledWith({ itemId: 'shirt', imageId: 'extra' });
  fireEvent.click(screen.getByLabelText('open', { exact: true }));
  const source = action === 'titles.deleteImage' ? '/redrawn.jpg' : item.image_url;
  await waitFor(() => expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', source));
});

it('resets to primary when comparison slides appear while an extra photo is active', async () => {
  const client = new QueryClient();
  const view = (value: Item) => <QueryClientProvider client={client}>
    <ItemDetailDialog item={value} open onOpenChange={vi.fn()} />
  </QueryClientProvider>;
  const { rerender } = render(view(item));
  fireEvent.click(document.querySelector('button img[src="/extra.webp"]')!.closest('button')!);
  rerender(view(customItem));
  fireEvent.click(screen.getByLabelText('open', { exact: true }));
  await waitFor(() => expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', item.image_url));
});

it('resets an open viewer when stored comparison photos arrive', async () => {
  const client = new QueryClient();
  const view = (value: Item) => <QueryClientProvider client={client}>
    <ItemDetailDialog item={value} open onOpenChange={vi.fn()} />
  </QueryClientProvider>;
  const { rerender } = render(view(item));
  fireEvent.click(document.querySelector('button img[src="/extra.webp"]')!.closest('button')!);
  fireEvent.click(screen.getByLabelText('open', { exact: true }));
  await waitFor(() => expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', '/extra.webp'));
  rerender(view(customItem));
  await waitFor(() => expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', item.image_url));
});

it('keeps the active comparison when an extra photo is deleted', async () => {
  render(<QueryClientProvider client={new QueryClient()}>
    <ItemDetailDialog item={customItem} open onOpenChange={vi.fn()} />
  </QueryClientProvider>);
  fireEvent.click(screen.getByTitle('actions.editItem'));
  fireEvent.click(screen.getByRole('button', { name: 'photoViewer.redrawn', exact: true }));
  fireEvent.click(screen.getByTitle('titles.deleteImage'));
  fireEvent.click(screen.getByLabelText('open', { exact: true }));
  await waitFor(() => expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', '/redrawn.jpg'));
});
