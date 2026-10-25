import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { ItemPhotoViewer } from '@/components/item-photo-viewer';

afterEach(cleanup);
const images = [{ id: 'primary', url: 'https://example.test/full.jpg?signature=original' }, { id: 'extra', url: 'https://example.test/extra.webp' }];
function show(index = 0) {
  return render(<ItemPhotoViewer images={images} index={index} alt="Shirt"><span>Photo</span></ItemPhotoViewer>);
}
function open() { fireEvent.click(screen.getByRole('button', { name: 'open' })); }

it('opens the active photo with the unresized signed source', async () => {
  show(1);
  expect(screen.queryByRole('dialog')).toBeNull();
  open();
  const dialog = await screen.findByRole('dialog', { name: 'Shirt' });
  expect(dialog.querySelector('img')).toHaveAttribute('src', images[1].url);
  expect(dialog.querySelector('img')).not.toHaveAttribute('srcset');
});

it('cycles photos with buttons and arrow keys', async () => {
  show(); open();
  const dialog = await screen.findByRole('dialog', { name: 'Shirt' });
  fireEvent.click(screen.getByRole('button', { name: 'next' }));
  await waitFor(() => expect(dialog.querySelector('.yarl__slide_current img')).toHaveAttribute('src', images[1].url));
  fireEvent.keyDown(dialog.querySelector('.yarl__container')!, { key: 'ArrowRight', code: 'ArrowRight' });
  await waitFor(() => expect(dialog.querySelector('.yarl__slide_current img')).toHaveAttribute('src', images[0].url));
  fireEvent.click(screen.getByRole('button', { name: 'previous' }));
  await waitFor(() => expect(dialog.querySelector('.yarl__slide_current img')).toHaveAttribute('src', images[1].url));
});

it.each(['close', 'escape', 'backdrop'])('closes via %s and restores focus', async (method) => {
  show(); open();
  const dialog = await screen.findByRole('dialog', { name: 'Shirt' });
  if (method === 'close') fireEvent.click(screen.getByRole('button', { name: 'close' }));
  if (method === 'escape') fireEvent.keyDown(dialog.querySelector('.yarl__container')!, { key: 'Escape', code: 'Escape' });
  if (method === 'backdrop') {
    const backdrop = dialog.querySelector('.yarl__slide_current')!;
    fireEvent.pointerDown(backdrop, { pointerId: 1, clientX: 0, clientY: 0 });
    fireEvent.pointerUp(backdrop, { pointerId: 1, clientX: 0, clientY: 0 });
  }
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(screen.getByRole('button', { name: 'open' })).toHaveFocus();
});

it('reopens with the updated active source', async () => {
  const view = show(); open();
  fireEvent.click(await screen.findByRole('button', { name: 'close' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  view.rerender(<ItemPhotoViewer images={[{ id: 'primary', url: '/redrawn.jpg' }]} index={0} alt="Shirt">Photo</ItemPhotoViewer>);
  open();
  expect((await screen.findByRole('dialog', { name: 'Shirt' })).querySelector('img')).toHaveAttribute('src', '/redrawn.jpg');
});

 it.each([{ sources: [] }, { sources: [{ id: 'missing', url: '' }] }])('does not open without a source: %j', ({ sources }) => {
  render(<ItemPhotoViewer images={sources} index={0} alt="Shirt">Photo</ItemPhotoViewer>);
  expect(screen.getByRole('button', { name: 'open' })).toBeDisabled();
  expect(screen.queryByRole('dialog')).toBeNull();
});
