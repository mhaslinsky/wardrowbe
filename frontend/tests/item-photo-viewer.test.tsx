import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ItemPhotoViewer } from '@/components/item-photo-viewer';

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
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

 it.each([{ sources: [{ id: 'missing', url: '' }] }])('does not open without a source: %j', ({ sources }) => {
  render(<ItemPhotoViewer images={sources} index={0} alt="Shirt">Photo</ItemPhotoViewer>);
  expect(screen.getByRole('button', { name: 'open' })).toBeDisabled();
  expect(screen.queryByRole('dialog')).toBeNull();
});

async function loadLargePhoto() {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(600);
  vi.spyOn(HTMLImageElement.prototype, 'naturalWidth', 'get').mockReturnValue(1600);
  vi.spyOn(HTMLImageElement.prototype, 'naturalHeight', 'get').mockReturnValue(2000);
  vi.stubGlobal('PointerEvent', class extends MouseEvent {
    pointerId: number;
    pointerType: string;
    constructor(type: string, options: PointerEventInit) {
      super(type, options);
      this.pointerId = options.pointerId ?? 1;
      this.pointerType = options.pointerType ?? 'mouse';
    }
  });
  show(); open();
  const photo = document.querySelector('.yarl__slide_current img')!;
  await act(async () => { fireEvent.load(photo); await vi.runOnlyPendingTimersAsync(); });
  return photo;
}

it('keeps actual-size zoom after the click following a pan', async () => {
  const photo = await loadLargePhoto();
  fireEvent.click(photo);
  const scale = () => Number.parseFloat((photo.parentElement!.style.transform).split('(')[1]);
  expect(scale()).toBeGreaterThan(1);
  fireEvent.pointerDown(photo, { pointerId: 1, clientX: 200, clientY: 200, buttons: 1 });
  fireEvent.pointerMove(photo, { pointerId: 1, clientX: 280, clientY: 240, buttons: 1 });
  fireEvent.pointerUp(photo, { pointerId: 1, clientX: 280, clientY: 240 });
  fireEvent.click(photo, { detail: 1 });
  expect(scale()).toBeGreaterThan(1);
});

it('retains the current photo on a parent rerender', async () => {
  const view = show(); open();
  fireEvent.click(screen.getByLabelText('next'));
  const current = () => document.querySelector('.yarl__slide_current img');
  await waitFor(() => expect(current()).toHaveAttribute('src', images[1].url));
  view.rerender(<ItemPhotoViewer images={images.map((image) => ({ ...image }))} index={0} alt="Shirt">Photo</ItemPhotoViewer>);
  expect(current()).toHaveAttribute('src', images[1].url);
});

it('toggles actual size with a focusable control and keyboard shortcut', async () => {
  const photo = await loadLargePhoto();
  const control = screen.getByLabelText('actualSize');
  control.focus();
  expect(control).toHaveFocus();
  expect(control).toHaveAttribute('aria-keyshortcuts', 'Z');
  fireEvent.keyDown(control, { key: 'z' });
  expect(screen.getByLabelText('fit')).toHaveAttribute('aria-pressed', 'true');
  expect(Number.parseFloat(photo.parentElement!.style.transform.split('(')[1])).toBeGreaterThan(1);
  fireEvent.keyDown(control, { key: 'Z' });
  expect(screen.getByLabelText('actualSize')).toHaveAttribute('aria-pressed', 'false');
});
