import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ItemPicker } from '@/components/shared/item-picker';

const response = vi.hoisted(() => ({ data: { items: [
    { id: 'underwear', name: 'Hidden underwear', type: 'underwear' },
    { id: 'shirt', name: 'Visible shirt', type: 'shirt' },
    { id: 'socks', name: 'Visible socks', type: 'socks' },
  ], has_more: false, total: 3 }, isLoading: false, isFetching: false }));
vi.mock('@/lib/hooks/use-items', () => ({ useItems: () => response }));
vi.mock('@/lib/hooks/use-translated-constants', () => ({ useTypeLabel: () => (value: string) => value }));

afterEach(cleanup);
it('hides underwear when limited to outfit types and preserves other items', () => {
  render(<ItemPicker selectedIds={new Set()} onToggle={vi.fn()} outfitTypesOnly />);
  expect(screen.queryByText('Hidden underwear')).not.toBeInTheDocument();
  expect(screen.getByText('Visible shirt')).toBeInTheDocument();
  expect(screen.getByText('Visible socks')).toBeInTheDocument();
});

it('shows underwear by default, so a hand-built outfit can include it', () => {
  render(<ItemPicker selectedIds={new Set()} onToggle={vi.fn()} />);
  expect(screen.getByText('Hidden underwear')).toBeInTheDocument();
  expect(screen.getByText('Visible shirt')).toBeInTheDocument();
});
