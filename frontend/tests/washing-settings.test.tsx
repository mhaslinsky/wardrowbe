import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { WashingSettings } from '@/components/washing-settings';

const state = vi.hoisted(() => ({ types: [{ type: 'socks', count: 2 }] }));
vi.mock('@/lib/hooks/use-items', () => ({
  useItemTypes: () => ({ data: state.types, isLoading: false, isError: false }),
}));
vi.mock('@/lib/hooks/use-translated-constants', () => ({
  useTypeLabel: () => (type: string) => type === 'socks' ? 'Socks' : 'Shirt',
}));
const change = vi.fn();
const defaults = { socks: 1, shirt: 3 };

beforeEach(() => {
  change.mockClear();
  state.types = [{ type: 'socks', count: 2 }];
});

it('shows only owned types and their current setting', () => {
  render(<WashingSettings values={{ socks: 2 }} defaults={defaults} onChange={change} />);
  expect(screen.getByRole('combobox', { name: 'Socks' })).toHaveValue('2');
  expect(screen.queryByRole('combobox', { name: 'Shirt' })).not.toBeInTheDocument();
});

it('offers 1 to 30 wears and Never', () => {
  render(<WashingSettings values={{}} defaults={defaults} onChange={change} />);
  const select = screen.getByRole('combobox', { name: 'Socks' });
  expect(select).toHaveValue('1');
  expect(screen.getAllByRole('option')).toHaveLength(31);
  fireEvent.change(select, { target: { value: '999' } });
  expect(change).toHaveBeenCalledWith({ socks: 999 });
});

it('resets a type without removing another type setting', () => {
  render(<WashingSettings values={{ socks: 2, shirt: 5 }} defaults={defaults} onChange={change} />);
  fireEvent.click(screen.getByRole('button', { name: 'washing.reset' }));
  expect(change).toHaveBeenCalledWith({ shirt: 5 });
});

it.each([{ types: [] }, { types: [{ type: 'unknown', count: 1 }] }])('shows an empty state without supported owned types: %j', ({ types }) => {
  state.types = types;
  render(<WashingSettings values={{}} defaults={defaults} onChange={change} />);
  expect(screen.getByText('washing.empty')).toBeInTheDocument();
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
});
