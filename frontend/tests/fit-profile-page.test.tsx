import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }));
vi.mock('@/lib/api', async (original) => ({ ...await original<typeof import('@/lib/api')>(), api: state }));
it('loads the saved profile and saves edits from its own page', async () => {
  const { default: FitProfilePage } = await import('@/app/dashboard/fit-profile/page');
  state.get.mockResolvedValue({ rev: 'existing', brand_sizes: [], skip_list: [], neck: null, sleeve: null, thigh: null, reference_garment: 'Saved shirt' });
  state.patch.mockImplementation(async (_path, profile) => ({ ...profile, rev: 'saved' }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><FitProfilePage /></QueryClientProvider>);
  const reference = await screen.findByLabelText('referenceGarment');
  expect(reference).toHaveValue('Saved shirt');
  fireEvent.change(reference, { target: { value: 'Updated shirt' } });
  fireEvent.click(screen.getByRole('button', { name: 'save' }));
  await waitFor(() => expect(state.patch).toHaveBeenCalledWith('/users/me/preferences/fit-profile', expect.objectContaining({ rev: 'existing', reference_garment: 'Updated shirt' })));
});
