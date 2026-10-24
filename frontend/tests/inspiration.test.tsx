import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import InspirationPage from '@/app/dashboard/inspiration/page';

const state = vi.hoisted(() => ({
  looks: { data: [] as unknown[], isPending: false, isError: false },
  create: { mutate: vi.fn(), reset: vi.fn(), isPending: false, isError: false, isSuccess: false },
  remove: { mutate: vi.fn(), isPending: false, isError: false },
}));
vi.mock('next-intl', () => ({ useTranslations: () => (key: string, values?: { value: number }) => values ? `${key}: ${values.value}%` : key }));
vi.mock('@/lib/hooks/use-inspiration', () => ({ useInspiration: () => state }));
beforeEach(() => {
  Object.assign(state.looks, { data: [], isPending: false, isError: false });
  Object.assign(state.create, { isPending: false, isError: false, isSuccess: false });
  state.remove.isPending = false;
  state.remove.isError = false;
  vi.clearAllMocks();
});

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

it('shows matched item names, strength and added base layers', () => {
  state.looks.data = [{ id: 'look1', image_url: '/photo.jpg',
    slots: [{ type: 'shirt', color: 'blue', fit: 'regular', pattern: 'solid',
      item_id: 'shirt1', match_strength: 0.86, no_match: false }],
    items: [{ id: 'shirt1', name: 'Blue shirt', image_url: '/shirt.jpg' },
      { id: 'base1', name: 'Undershirt', image_url: '/base.jpg' }] }];
  render(<InspirationPage />);
  expect(screen.getByText('Blue shirt')).toBeVisible();
  expect(screen.getByAltText('Blue shirt')).toHaveAttribute('src', '/shirt.jpg');
  expect(screen.getByText('strength: 86%')).toBeVisible();
  expect(screen.queryByText('noMatch')).not.toBeInTheDocument();
  expect(screen.getByText('baseLayer')).toBeVisible();
});

it('blocks repeated uploads and deletion during requests', () => {
  state.create.isPending = true;
  state.remove.isPending = true;
  state.looks.data = [{ id: 'look1', image_url: '/photo.jpg', slots: [], items: [] }];
  render(<InspirationPage />);
  expect(screen.getByLabelText('photo')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'working' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'delete' })).toBeDisabled();
});

it('shows loading, empty, saved and error states', () => {
  state.looks.isPending = true;
  const view = render(<InspirationPage />);
  expect(screen.getByText('loading')).toBeVisible();
  state.looks.isPending = false;
  view.rerender(<InspirationPage />);
  expect(screen.getByText('empty')).toBeVisible();
  state.create.isError = true;
  view.rerender(<InspirationPage />);
  expect(screen.getByRole('alert')).toBeVisible();
  state.create.isError = false;
  state.create.isSuccess = true;
  view.rerender(<InspirationPage />);
  expect(screen.getByText('saved')).toBeVisible();
});
