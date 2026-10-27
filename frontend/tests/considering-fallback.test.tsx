import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import ImportPage from '@/app/dashboard/considering/import/page';
import { api } from '@/lib/api';
vi.mock('@/lib/api', () => ({ api: { post: vi.fn(), patch: vi.fn(), get: vi.fn() }, ApiError: class extends Error {} }));
vi.mock('@/lib/hooks/use-user', () => { const user = { id: 'user', email: 'user@example.test' }; return { useUserProfile: () => ({ data: user }) }; });
vi.mock('@tanstack/react-query', () => ({ useQueryClient: () => ({ invalidateQueries: vi.fn() }) }));
const product = { source_url: 'https://shop.example/product', name: 'Shirt', images: ['https://shop.example/image.png'], sizes: [], colors: [] };
beforeEach(() => { vi.clearAllMocks(); history.replaceState({}, '', '/dashboard/considering/import'); });
it('pastes a link, confirms, and creates only after save', async () => {
  vi.mocked(api.post).mockResolvedValueOnce(product).mockResolvedValueOnce({ item: { id: 'item' } });
  vi.mocked(api.patch).mockResolvedValue({}); vi.mocked(api.get).mockResolvedValue({ item: { id: 'item' } });
  render(<ImportPage />);
  fireEvent.change(await screen.findByLabelText('link'), { target: { value: product.source_url } });
  fireEvent.click(screen.getByText('readLink'));
  await screen.findByDisplayValue('Shirt');
  expect(api.post).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText('save'));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/considering', expect.objectContaining({ source_url: product.source_url })));
});
it('cancels an extracted link without a create', async () => {
  vi.mocked(api.post).mockResolvedValue(product);
  render(<ImportPage />);
  fireEvent.change(await screen.findByLabelText('link'), { target: { value: product.source_url } });
  fireEvent.click(screen.getByText('readLink')); await screen.findByDisplayValue('Shirt');
  fireEvent.click(screen.getByText('cancel'));
  await waitFor(() => expect(screen.getByLabelText('link')).toBeInTheDocument());
  expect(api.post).toHaveBeenCalledTimes(1);
});
it('offers image input after an empty or blocked page', async () => {
  vi.mocked(api.post).mockRejectedValue(Error('blocked'));
  render(<ImportPage />);
  fireEvent.change(await screen.findByLabelText('link'), { target: { value: product.source_url } });
  fireEvent.click(screen.getByText('readLink'));
  expect(await screen.findByRole('alert')).toHaveTextContent('linkFailed');
  expect(screen.getByLabelText('imageDrop')).toBeInTheDocument();
});
