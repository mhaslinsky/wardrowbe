import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
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
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); });
afterEach(() => { cleanup(); vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); });

function comparisonButton(name: string) {
  const button = screen.getByText(name).closest('button')!;
  expect(button).toHaveRole('button');
  expect(button).toHaveAccessibleName(name);
  expect(button).toBeVisible();
  return button;
}

async function interact(action: () => void) {
  await act(async () => { action(); });
  await act(async () => { vi.runOnlyPendingTimers(); });
}
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
  await interact(() => { fireEvent.click(trigger); });
  const current = () => document.querySelector('.yarl__slide_current img');
  expect(current()).toHaveAttribute('src', item.image_url);
  expect(current()).not.toHaveAttribute('srcset');
  await interact(() => { fireEvent.click(screen.getByLabelText('next', { exact: true })); });
  expect(current()).toHaveAttribute('src', '/extra.webp');
  const container = document.querySelector('.yarl__container')!;
  await interact(() => { fireEvent.keyDown(container, { key: 'ArrowRight' }); });
  expect(current()).toHaveAttribute('src', item.image_url);
  if (method === 'close') await interact(() => { fireEvent.click(screen.getByLabelText('close', { exact: true })); });
  if (method === 'escape') await interact(() => { fireEvent.keyDown(container, { key: 'Escape' }); });
  if (method === 'backdrop') {
    const backdrop = document.querySelector('.yarl__slide_current')!;
    await interact(() => { fireEvent.pointerDown(backdrop, { pointerId: 1 }); });
    await interact(() => { fireEvent.pointerUp(backdrop, { pointerId: 1 }); });
  }
  await act(async () => { vi.runOnlyPendingTimers(); });
  expect(document.querySelector('.yarl__root')).toBeNull();
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
    await interact(() => { fireEvent.click(comparisonButton(`photoViewer.${alternate}`)); });
    await interact(() => { fireEvent.click(screen.getByLabelText('open', { exact: true })); });
    expect(document.querySelector('.yarl__slide_current img'))
      .toHaveAttribute('src', comparisonItem.photo_choices![alternate]);
    expect(document.querySelector('.yarl__slide_current [data-testid=photo-version-label]')).toHaveTextContent(alternate);
    expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('alt', 'versionAlt');
    expect(request).not.toHaveBeenCalled();
    await interact(() => { fireEvent.click(screen.getByLabelText('next', { exact: true })); });
    expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', '/extra.webp');
  });

it.each([undefined, {}, { original: '/original.jpg' }, { redrawn: '/redrawn.jpg' }])(
  'keeps single-version and missing choices unchanged: %j', (choices) => {
    render(<QueryClientProvider client={new QueryClient()}>
      <ItemDetailDialog item={{ ...item, additional_images: [], photo_choices: choices } as Item} open onOpenChange={vi.fn()} />
    </QueryClientProvider>);
    expect(screen.queryByRole('button', { name: 'photoViewer.original' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'photoViewer.redrawn' })).toBeNull();
  });

const customItem = { ...item, photo_redraw: { selected: 'custom' },
  photo_choices: { original: '/original.jpg', redrawn: '/redrawn.jpg' } } as Item;

it('shows both stored versions after a custom primary photo', async () => {
  render(<QueryClientProvider client={new QueryClient()}>
    <ItemDetailDialog item={customItem} open onOpenChange={vi.fn()} />
  </QueryClientProvider>);
  await interact(() => { fireEvent.click(comparisonButton('photoViewer.original')); });
  await interact(() => { fireEvent.click(screen.getByLabelText('open', { exact: true })); });
  expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', '/original.jpg');
  await interact(() => { fireEvent.click(screen.getByLabelText('next', { exact: true })); });
  expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', '/redrawn.jpg');
});

it.each(['titles.deleteImage', 'titles.setAsPrimary'])('keeps extra-photo actions correct with comparison slides: %s', async (action) => {
  render(<QueryClientProvider client={new QueryClient()}>
    <ItemDetailDialog item={customItem} open onOpenChange={vi.fn()} />
  </QueryClientProvider>);
  await interact(() => { fireEvent.click(screen.getByTitle('actions.editItem')); });
  const extra = document.querySelector('button img[src="/extra.webp"]')!;
  await interact(() => { fireEvent.click(extra.closest('button')!); });
  await interact(() => { fireEvent.click(screen.getByTitle(action)); });
  const mutation = action === 'titles.deleteImage' ? mutations.deleteImage : mutations.setPrimary;
  expect(mutation).toHaveBeenCalledWith({ itemId: 'shirt', imageId: 'extra' });
  await interact(() => { fireEvent.click(screen.getByLabelText('open', { exact: true })); });
  const source = action === 'titles.deleteImage' ? '/redrawn.jpg' : item.image_url;
  expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', source);
});

it('resets to primary when comparison slides appear while an extra photo is active', async () => {
  const client = new QueryClient();
  const view = (value: Item) => <QueryClientProvider client={client}>
    <ItemDetailDialog item={value} open onOpenChange={vi.fn()} />
  </QueryClientProvider>;
  const { rerender } = render(view(item));
  await interact(() => { fireEvent.click(document.querySelector('button img[src="/extra.webp"]')!.closest('button')!); });
  expect(screen.getByLabelText('open', { exact: true }).querySelector('img')).toHaveAttribute('src', '/extra.webp');
  await interact(() => { rerender(view(customItem)); });
  await interact(() => { fireEvent.click(screen.getByLabelText('open', { exact: true })); });
  expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', item.image_url);
});

it('resets an open viewer when stored comparison photos arrive', async () => {
  const client = new QueryClient();
  const view = (value: Item) => <QueryClientProvider client={client}>
    <ItemDetailDialog item={value} open onOpenChange={vi.fn()} />
  </QueryClientProvider>;
  const { rerender } = render(view(item));
  await interact(() => { fireEvent.click(document.querySelector('button img[src="/extra.webp"]')!.closest('button')!); });
  await interact(() => { fireEvent.click(screen.getByLabelText('open', { exact: true })); });
  expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', '/extra.webp');
  await interact(() => { rerender(view(customItem)); });
  expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', item.image_url);
});

it('keeps the active comparison when an extra photo is deleted', async () => {
  render(<QueryClientProvider client={new QueryClient()}>
    <ItemDetailDialog item={customItem} open onOpenChange={vi.fn()} />
  </QueryClientProvider>);
  await interact(() => { fireEvent.click(screen.getByTitle('actions.editItem')); });
  await interact(() => { fireEvent.click(comparisonButton('photoViewer.redrawn')); });
  expect(screen.getByLabelText('open', { exact: true }).querySelector('img')).toHaveAttribute('src', '/redrawn.jpg');
  await interact(() => { fireEvent.click(screen.getByTitle('titles.deleteImage')); });
  expect(mutations.deleteImage).toHaveBeenCalledWith({ itemId: 'shirt', imageId: 'extra' });
  await interact(() => { fireEvent.click(screen.getByLabelText('open', { exact: true })); });
  expect(document.querySelector('.yarl__slide_current img')).toHaveAttribute('src', '/redrawn.jpg');
});


it('hides outfit generation for underwear and restores actions for other types', () => {
  const client = new QueryClient();
  const view = (type: string) => <QueryClientProvider client={client}>
    <ItemDetailDialog item={{ ...item, type }} open onOpenChange={vi.fn()} />
  </QueryClientProvider>;
  const { rerender } = render(view('underwear'));
  for (const type of ['underwear', 'shirt', 'socks', 'underwear']) {
    rerender(view(type));
    for (const title of ['titles.suggestOutfit', 'titles.findMatchingOutfits']) {
      if (type === 'underwear') expect(screen.queryByTitle(title)).not.toBeInTheDocument();
      else expect(screen.getByTitle(title)).toBeInTheDocument();
    }
  }
});
