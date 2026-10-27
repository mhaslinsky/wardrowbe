import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import ImportPage from '@/app/dashboard/considering/import/page';
import { api } from '@/lib/api';
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), api: { post: vi.fn(), patch: vi.fn(), get: vi.fn() } }));
let user = { id: 'user', email: 'user@example.test' };
vi.mock('@/lib/hooks/use-user', () => ({ useUserProfile: () => ({ data: user }) }));
vi.mock('@tanstack/react-query', () => ({ useQueryClient: () => ({ invalidateQueries: vi.fn() }) }));
const product = { source_url: 'https://shop.example/product', name: 'Shirt', images: ['https://shop.example/image.png'], sizes: [], colors: [] };
const item = { id: 'item', user_id: 'user', tags: {}, notes: null };
function readLink() {
  fireEvent.change(screen.getByLabelText('link'), { target: { value: product.source_url } });
  fireEvent.click(screen.getByText('readLink'));
}
beforeEach(() => {
  vi.clearAllMocks(); sessionStorage.clear(); user = { id: 'user', email: 'user@example.test' };
  URL.createObjectURL = vi.fn(() => 'blob:preview'); URL.revokeObjectURL = vi.fn();
  vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 48, height: 64, close: vi.fn() }));
  vi.mocked(api.patch).mockResolvedValue(item);
  vi.mocked(api.get).mockImplementation(async path => path === '/considering' ? [{ state: 'considering', item }] : item);
});
it('pastes a link, confirms, and creates only after save', async () => {
  vi.mocked(api.post).mockResolvedValueOnce(product).mockResolvedValueOnce({ state: 'considering', item });
  render(<ImportPage />); readLink(); await screen.findByDisplayValue('Shirt');
  expect(api.post).toHaveBeenCalledTimes(1); expect(sessionStorage.length).toBe(0);
  fireEvent.click(screen.getByText('save'));
  await waitFor(() => expect(api.get).toHaveBeenCalledWith('/considering'));
  expect(api.post).toHaveBeenCalledWith('/considering', expect.objectContaining({ source_url: product.source_url }));
});
it('cancels an extracted link without creating anything', async () => {
  vi.mocked(api.post).mockResolvedValue(product);
  render(<ImportPage />); readLink(); await screen.findByDisplayValue('Shirt');
  fireEvent.click(screen.getByText('cancel'));
  await screen.findByLabelText('link'); expect(api.post).toHaveBeenCalledTimes(1);
});
it.each(['', 'not a URL', 'file:///tmp/photo', 'https://user:password@shop.example'])('rejects empty or malformed input: %s', async value => {
  render(<ImportPage />); fireEvent.change(screen.getByLabelText('link'), { target: { value } });
  fireEvent.click(screen.getByText('readLink')); await screen.findByRole('alert');
  expect(api.post).not.toHaveBeenCalled();
});
it.each([null, { ...product, images: [] }])('offers image input for an unreadable or empty page', async result => {
  if (result) vi.mocked(api.post).mockResolvedValue(result); else vi.mocked(api.post).mockRejectedValue(Error('blocked'));
  render(<ImportPage />); readLink(); expect(await screen.findByRole('alert')).toHaveTextContent('linkFailed');
  expect(screen.getByLabelText('imageDrop')).toBeVisible(); expect(api.post).toHaveBeenCalledTimes(1);
});
it('discards a delayed response after reset', async () => {
  let finish!: (value: unknown) => void;
  vi.mocked(api.post).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  render(<ImportPage />); readLink(); fireEvent.click(screen.getByText('cancel'));
  await act(async () => finish(product));
  expect(screen.getByLabelText('link')).toHaveValue(''); expect(screen.queryByDisplayValue('Shirt')).not.toBeInTheDocument();
});
it('keeps a dropped file in memory and releases its preview on cancel', async () => {
  render(<ImportPage />);
  const file = new File(['png'], 'shirt.png', { type: 'image/png' });
  fireEvent.change(screen.getByLabelText('imageDrop'), { target: { files: [file] } });
  await screen.findByLabelText('name'); expect(screen.getByRole('img')).toHaveAttribute('src', 'blob:preview');
  expect(fetch).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled(); expect(sessionStorage.length).toBe(0);
  fireEvent.click(screen.getByText('cancel')); await screen.findByLabelText('link');
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview');
});
it('rejects malformed image bytes without creating a preview or item', async () => {
  vi.mocked(createImageBitmap).mockRejectedValue(Error('decode'));
  render(<ImportPage />); fireEvent.change(screen.getByLabelText('imageDrop'), { target: { files: [new File(['broken'], 'shirt.png', { type: 'image/png' })] } });
  expect(await screen.findByRole('alert')).toHaveTextContent('imageFailed');
  expect(URL.createObjectURL).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
});
it('shows a recovery error for malformed stored drafts', async () => {
  sessionStorage.setItem('considering-import:user', '{broken');
  render(<ImportPage />); expect(await screen.findByRole('alert')).toHaveTextContent('recoveryFailed');
});

it('clears a file preview before opening a link in a new account', async () => {
  const view = render(<ImportPage />);
  fireEvent.change(screen.getByLabelText('imageDrop'), { target: { files: [new File(['png'], 'shirt.png', { type: 'image/png' })] } });
  await screen.findByLabelText('name');
  user = { id: 'other', email: 'other@example.test' }; view.rerender(<ImportPage />);
  await screen.findByLabelText('link'); vi.mocked(api.post).mockResolvedValue(product); readLink();
  await screen.findByDisplayValue('Shirt');
  expect(screen.getByRole('img')).toHaveAttribute('src', product.images[0]);
});
it('offers checked discard for an invalid stored capture and resets it', async () => {
  sessionStorage.setItem('considering-import:user', JSON.stringify({ capture: null, fileBacked: false }));
  render(<ImportPage />); expect(await screen.findByRole('alert')).toHaveTextContent('recoveryFailed');
  fireEvent.click(screen.getByText('discardChecked'));
  expect(sessionStorage.length).toBe(0); expect(screen.getByLabelText('link')).toBeEnabled();
});
