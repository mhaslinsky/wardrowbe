import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '@/lib/api';
import { createQueryClient } from '@/lib/query-client';
import { toast } from 'sonner';
import { FitProfileSettings } from '@/components/fit-profile-settings';

const state = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }));
vi.mock('@/lib/api', async (original) => ({ ...await original<typeof import('@/lib/api')>(), api: state }));
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }));
function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>{children}</QueryClientProvider>;
}
const initial = { rev: 'old', brand_sizes: [], skip_list: [], neck: null, sleeve: null, thigh: null, reference_garment: '' };
beforeEach(() => {
  vi.mocked(toast.error).mockClear();
  state.get.mockReset();
  state.get.mockResolvedValue(structuredClone(initial));
  state.patch.mockReset();
});

it('adds, edits and deletes brand sizes and skip rows', async () => {
  state.patch.mockImplementation(async (_path, profile) => ({ ...profile, rev: 'saved' }));
  render(<FitProfileSettings />, { wrapper });
  fireEvent.click(await screen.findByRole('button', { name: 'addBrand' }));
  fireEvent.change(screen.getByLabelText('brand'), { target: { value: 'FLX' } });
  fireEvent.change(screen.getByLabelText('size'), { target: { value: 'L' } });
  fireEvent.click(screen.getByRole('button', { name: 'addSkip' }));
  fireEvent.change(screen.getAllByLabelText('line')[1], { target: { value: 'Slim' } });
  fireEvent.change(screen.getByLabelText('reason'), { target: { value: 'Too narrow' } });
  fireEvent.change(screen.getByLabelText('neck'), { target: { value: '40' } });
  fireEvent.click(screen.getByRole('button', { name: 'save' }));
  await waitFor(() => expect(state.patch).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
    neck: 40, brand_sizes: [{ brand: 'FLX', line: '', size: 'L', note: '' }],
    skip_list: [{ brand: '', line: 'Slim', reason: 'Too narrow' }],
  })));
  fireEvent.change(screen.getByLabelText('size'), { target: { value: 'XL' } });
  fireEvent.click(screen.getByRole('button', { name: 'save' }));
  await waitFor(() => expect(state.patch).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({ rev: 'saved', brand_sizes: [expect.objectContaining({ size: 'XL' })] })));
  fireEvent.click(screen.getAllByRole('button', { name: 'remove' })[0]);
  fireEvent.click(screen.getByRole('button', { name: 'remove' }));
  fireEvent.change(screen.getByLabelText('neck'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'save' }));
  await waitFor(() => expect(state.patch).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({ neck: null, brand_sizes: [], skip_list: [] })));
});

it('keeps the draft after a conflict and reloads only on request', async () => {
  state.patch.mockRejectedValue({ status: 409 });
  render(<FitProfileSettings />, { wrapper });
  const reference = await screen.findByLabelText('referenceGarment');
  fireEvent.change(reference, { target: { value: 'Keep my draft' } });
  fireEvent.click(screen.getByRole('button', { name: 'save' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('conflict');
  expect(reference).toHaveValue('Keep my draft');
  fireEvent.click(screen.getByRole('button', { name: 'reload' }));
  await waitFor(() => expect(reference).toHaveValue(''));
});

it('shows a failed read and allows retry', async () => {
  state.get.mockRejectedValueOnce(new Error('offline'));
  render(<FitProfileSettings />, { wrapper });
  expect(await screen.findByRole('alert')).toHaveTextContent('failed');
  fireEvent.click(screen.getByRole('button', { name: 'reload' }));
  expect(await screen.findByLabelText('referenceGarment')).toHaveValue('');
});

it('reports malformed responses instead of rendering a broken editor', async () => {
  state.get.mockResolvedValueOnce({ image_url: '/photo.jpg' });
  render(<FitProfileSettings />, { wrapper });
  expect(await screen.findByRole('alert')).toHaveTextContent('failed');
  expect(screen.queryByRole('button', { name: 'save' })).not.toBeInTheDocument();
});


it('keeps a dirty draft and its revision when the query cache refreshes', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><FitProfileSettings /></QueryClientProvider>);
  const reference = await screen.findByLabelText('referenceGarment');
  fireEvent.change(reference, { target: { value: 'My unsaved draft' } });
  act(() => client.setQueryData(['fit-profile', undefined], { ...initial, rev: 'newer', reference_garment: 'Server update' }));
  expect(reference).toHaveValue('My unsaved draft');
  state.patch.mockRejectedValue({ status: 409 });
  fireEvent.click(screen.getByRole('button', { name: 'save' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('conflict');
  expect(state.patch).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ rev: 'old', reference_garment: 'My unsaved draft' }));
});

it('keeps the dirty draft when an explicit reload fails', async () => {
  state.patch.mockRejectedValue({ status: 409 });
  render(<FitProfileSettings />, { wrapper });
  const reference = await screen.findByLabelText('referenceGarment');
  fireEvent.change(reference, { target: { value: 'Keep through failed reload' } });
  fireEvent.click(screen.getByRole('button', { name: 'save' }));
  await screen.findByRole('alert');
  state.get.mockRejectedValueOnce(new Error('offline'));
  fireEvent.click(screen.getByRole('button', { name: 'reload' }));
  await waitFor(() => expect(state.get).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByRole('button', { name: 'reload' })).toBeEnabled());
  expect(reference).toHaveValue('Keep through failed reload');
});


it('shows the translated inline conflict without a raw global toast', async () => {
  state.patch.mockRejectedValue(new ApiError('Fit profile changed', 409, {}));
  render(<QueryClientProvider client={createQueryClient()}><FitProfileSettings /></QueryClientProvider>);
  const reference = await screen.findByLabelText('referenceGarment');
  fireEvent.change(reference, { target: { value: 'Keep this draft' } });
  fireEvent.click(screen.getByRole('button', { name: 'save' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('conflict');
  expect(toast.error).not.toHaveBeenCalled();
});

it('shows the translated load error without a raw global toast', async () => {
  state.get.mockRejectedValue(new ApiError('Unavailable', 503, {}));
  render(<QueryClientProvider client={createQueryClient()}><FitProfileSettings /></QueryClientProvider>);
  expect(await screen.findByRole('alert')).toHaveTextContent('failed');
  expect(toast.error).not.toHaveBeenCalled();
});
