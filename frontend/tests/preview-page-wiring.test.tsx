import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DayOutfits } from '@/components/day-outfits';
import OutfitPage from '@/app/dashboard/outfits/[id]/page';
import SettingsPage from '@/app/dashboard/settings/page';
import type { Outfit } from '@/lib/types';

const state = vi.hoisted(() => ({ worn: false }));
const preferences = { standing_rules: [] };
const profile = { timezone: 'America/New_York' };
const outfit = { id: 'one', name: 'Layers', source: 'manual', status: 'pending', occasion: 'casual', scheduled_for: '2026-10-24', items: [{ id: 'shirt', name: 'Shirt', type: 'shirt', needs_wash: false }], feedback: null } as unknown as Outfit;
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }), useParams: () => ({ id: 'one' }) }));
vi.mock('@/lib/hooks/use-user', () => ({ useUserToday: () => () => '2026-10-24', useUserProfile: () => ({ data: profile }), useUpdateUserProfile: () => ({ mutateAsync: vi.fn() }) }));
vi.mock('@/lib/hooks/use-preferences', () => ({ usePreferences: () => ({ data: preferences }), useUpdatePreferences: () => ({ mutateAsync: vi.fn() }), useResetPreferences: () => ({}), useTestAIEndpoint: () => ({}) }));
vi.mock('@/lib/hooks/use-day-outfits', () => ({
  useDaySelection: () => ({ today: '2026-10-24', date: '2026-10-24', ready: true, setDate: vi.fn() }),
  useDayOutfits: () => ({ data: { pages: [{ total: 1, outfits: [{ ...outfit, feedback: state.worn ? { worn_at: '2026-10-24' } : null }] }] } }),
  useGenerateDayOptions: () => ({}), useLogDayWear: () => ({}),
}));
vi.mock('@/lib/hooks/use-outfits', () => ({
  useInfiniteOutfits: () => ({ data: { pages: [{ total: 0, outfits: [] }] } }),
  useRejectOutfit: () => ({}), useOutfit: () => ({ data: outfit }), useOutfits: () => ({}), useDeleteOutfit: () => ({}),
}));
vi.mock('@/lib/hooks/use-studio', () => ({ useWearToday: () => ({}) }));
vi.mock('@/components/undo-wear-actions', () => ({ UndoWearActions: () => null }));
vi.mock('@/components/shared/lineage-card', () => ({ LineageCard: () => null }));
vi.mock('@/components/shared/clone-to-lookbook-dialog', () => ({ CloneToLookbookDialog: () => null }));
vi.mock('@/components/ui/select', async (original) => ({ ...await original<typeof import('@/components/ui/select')>(), Select: () => null }));
function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{children}</QueryClientProvider>;
}
beforeEach(() => {
  vi.stubGlobal('localStorage', { getItem: vi.fn(), setItem: vi.fn() });
  state.worn = false;
  vi.mocked(fetch).mockReset().mockResolvedValue({ ok: true, status: 200, json: async () => ({ image_url: '/preview', medium_url: '/medium', stale: false }) } as Response);
});

it.each([false, true])('wires the preview on a day card with worn=%s', async (worn) => {
  state.worn = worn;
  render(<DayOutfits />, { wrapper });
  fireEvent.click(screen.getByRole('button', { name: 'see' }));
  const image = await screen.findByRole('img', { name: 'see' });
  expect(image.closest('.grid-cols-2')).toContainElement(screen.getByText('Layers'));
  expect(fetch).toHaveBeenCalledWith('/api/v1/outfits/one/preview', expect.objectContaining({ method: 'GET' }));
});

it('keeps the original outfit grid until a preview loads', async () => {
  render(<OutfitPage />, { wrapper });
  const item = screen.getByText('Shirt');
  expect(item.closest('.grid')).toHaveClass('sm:grid-cols-4', 'md:grid-cols-5');
  expect(item.closest('.sm\\:grid-cols-2')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'see' }));
  const image = await screen.findByRole('img', { name: 'see' });
  expect(image.closest('.sm\\:grid-cols-2')).toContainElement(item);
});

it('wires preview photo controls into Settings', async () => {
  render(<SettingsPage />, { wrapper });
  expect(await screen.findByRole('img', { name: 'title' })).toHaveAttribute('src', '/medium');
  expect(screen.getByLabelText('replace')).toBeEnabled();
  expect(fetch).toHaveBeenCalledWith('/api/v1/users/me/preview-photo', expect.objectContaining({ method: 'GET' }));
});

afterEach(() => vi.unstubAllGlobals());
