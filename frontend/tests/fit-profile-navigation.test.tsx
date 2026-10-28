import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { Sidebar } from '@/components/sidebar';
import { MobileSidebar } from '@/components/mobile-sidebar';
import SettingsPage from '@/app/dashboard/settings/page';

const state = vi.hoisted(() => ({ replace: vi.fn(), preferences: { standing_rules: [] } }));
vi.mock('next/navigation', () => ({ usePathname: () => '/dashboard/fit-profile', useRouter: () => state, useSearchParams: () => new URLSearchParams() }));
vi.mock('@/lib/hooks/use-preferences', () => ({
  usePreferences: () => ({ data: state.preferences, isLoading: false }),
  useUpdatePreferences: () => ({ mutateAsync: vi.fn() }),
  useResetPreferences: () => ({ mutateAsync: vi.fn() }),
  useTestAIEndpoint: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock('@/lib/hooks/use-user', () => ({ useUserProfile: () => ({ data: undefined }), useUpdateUserProfile: () => ({ mutateAsync: vi.fn() }) }));
vi.mock('@/components/ui/select', async (original) => ({ ...await original<typeof import('@/components/ui/select')>(), Select: () => null }));
vi.mock('@/components/fit-profile-settings', () => ({ FitProfileSettings: () => <div>Fit editor</div> }));
vi.mock('@/components/access-token-settings', () => ({ AccessTokenSettings: () => null }));
vi.mock('@/components/preview-photo-settings', () => ({ PreviewPhotoSettings: () => null }));

it('links to the active fit page from the desktop menu', () => {
  render(<Sidebar />);
  expect(screen.getByRole('link', { name: 'fitProfile' })).toHaveAttribute('href', '/dashboard/fit-profile');
  expect(screen.getByRole('link', { name: 'fitProfile' })).toHaveClass('bg-primary');
});
it('links from the phone menu and closes it', () => {
  const close = vi.fn();
  render(<MobileSidebar open onClose={close} />);
  fireEvent.click(screen.getByRole('link', { name: 'fitProfile' }));
  expect(close).toHaveBeenCalled();
});
it('replaces the Settings editor with a link', () => {
  render(<SettingsPage />);
  expect(screen.queryByText('Fit editor')).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'fitProfile' })).toHaveAttribute('href', '/dashboard/fit-profile');
});
it('redirects an old fit section link, including hash navigation', async () => {
  window.history.replaceState(null, '', '/dashboard/settings#fit-profile');
  render(<SettingsPage />);
  await waitFor(() => expect(state.replace).toHaveBeenCalledWith('/dashboard/fit-profile'));
  state.replace.mockClear();
  window.history.replaceState(null, '', '/dashboard/settings');
  fireEvent(window, new HashChangeEvent('hashchange'));
  expect(state.replace).not.toHaveBeenCalled();
  window.history.replaceState(null, '', '/dashboard/settings#fit-profile');
  fireEvent(window, new HashChangeEvent('hashchange'));
  expect(state.replace).toHaveBeenCalledWith('/dashboard/fit-profile');
  window.history.replaceState(null, '', '/');
});
