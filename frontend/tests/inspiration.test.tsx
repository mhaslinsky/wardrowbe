import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import InspirationPage from '@/app/dashboard/inspiration/page';

const state = vi.hoisted(() => ({
  looks: { data: [] as unknown[], isPending: false, isError: false },
  create: { mutate: vi.fn(), reset: vi.fn(), isPending: false, isError: false, isSuccess: false },
  remove: { mutate: vi.fn(), isPending: false, isError: false },
}));
vi.mock('@/lib/hooks/use-inspiration', () => ({ useInspiration: () => state }));
beforeEach(() => { state.looks.data = []; vi.clearAllMocks(); });

it('requires a photo, submits it, and resets errors when it changes', () => {
  render(<InspirationPage />);
  const button = screen.getByRole('button', { name: 'create' });
  expect(button).toBeDisabled();
  const file = new File(['photo'], 'look.jpg', { type: 'image/jpeg' });
  fireEvent.change(screen.getByLabelText('photo'), { target: { files: [file] } });
  expect(button).toBeEnabled();
  fireEvent.submit(button.closest('form')!);
  expect(state.create.reset).toHaveBeenCalledOnce();
  expect(state.create.mutate).toHaveBeenCalledWith(file);
});

it('shows gaps, matched garments, comparison photos and delete', () => {
  state.looks.data = [{ id: 'look1', image_url: '/photo.jpg',
    slots: [{ type: 'shirt', color: 'blue', fit: 'regular', pattern: 'solid',
      item_id: null, match_strength: 0, no_match: true }], items: [] }];
  render(<InspirationPage />);
  expect(screen.getByText('noMatch')).toBeVisible();
  expect(screen.getByAltText('photo')).toHaveAttribute('src', '/photo.jpg');
  expect(screen.getByRole('link', { name: 'open' })).toHaveAttribute('href', '/dashboard/outfits/look1');
  fireEvent.click(screen.getByRole('button', { name: 'delete' }));
  expect(state.remove.mutate).toHaveBeenCalledWith('look1');
});
