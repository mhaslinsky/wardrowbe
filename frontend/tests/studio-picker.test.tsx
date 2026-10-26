import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { ItemPicker } from '@/components/shared/item-picker';

const mock = vi.hoisted(() => ({
  pages: [[{ id: 'shirt', name: 'Blue shirt', type: 'shirt' },
    { id: 'socks', name: 'Blue socks', type: 'socks' },
    { id: 'dress', name: 'Blue dress', type: 'dress' }]],
  filters: {} as { search?: string },
  page: 1,
  cache: new Map(),
}));
vi.mock('@/lib/hooks/use-items', () => ({
  useItems: (filters: { search?: string }, page: number) => {
    mock.filters = filters;
    mock.page = page;
    const key = JSON.stringify([filters, page]);
    if (!mock.cache.has(key)) {
      const items = mock.pages[page - 1] ?? [];
      mock.cache.set(key, { data: { items: items.filter((item) => !filters.search || item.name.includes(filters.search)),
        has_more: page < mock.pages.length }, isLoading: false, isFetching: false });
    }
    return mock.cache.get(key);
  },
}));
vi.mock('@/lib/hooks/use-translated-constants', () => ({ useTypeLabel: () => (value: string) => value }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
beforeEach(() => {
  mock.cache.clear();
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(1000);
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(300);
  mock.pages = [[{ id: 'shirt', name: 'Blue shirt', type: 'shirt' },
    { id: 'socks', name: 'Blue socks', type: 'socks' },
    { id: 'dress', name: 'Blue dress', type: 'dress' }]];
});
function picker() {
  return render(<ItemPicker selectedIds={new Set()} onToggle={vi.fn()} slotFilters heightClass="flex-1 min-h-0 basis-0" />);
}
it('combines vocabulary roles and search, then resets to all', async () => {
  picker();
  fireEvent.click(screen.getByRole('button', { name: 'slots.tops' }));
  expect(screen.getByText('Blue dress')).toBeInTheDocument();
  expect(screen.queryByText('Blue socks')).not.toBeInTheDocument();
  fireEvent.change(screen.getByPlaceholderText('search'), { target: { value: 'dress' } });
  await waitFor(() => expect(mock.filters.search).toBe('dress'));
  await waitFor(() => expect(screen.queryByText('Blue shirt')).not.toBeInTheDocument());
  expect(screen.getByText('Blue dress')).toBeInTheDocument();
  fireEvent.change(screen.getByPlaceholderText('search'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'slots.all' }));
  await waitFor(() => expect(screen.getByText('Blue socks')).toBeInTheDocument());
});
it('loads through pages hidden by the selected slot', async () => {
  mock.pages = [mock.pages[0].slice(0, 1), [{ id: 'socks', name: 'Later socks', type: 'socks' }]];
  picker();
  fireEvent.click(screen.getByRole('button', { name: 'slots.socks' }));
  await waitFor(() => expect(screen.getByText('Later socks')).toBeInTheDocument());
  expect(mock.page).toBe(2);
});
it('handles unknown types and empty results without hiding All items', () => {
  mock.pages = [[{ id: 'unknown', name: 'Mystery', type: 'unknown' }]];
  picker();
  expect(screen.getByText('Mystery')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'slots.layers' }));
  expect(screen.getByText('noItems')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'slots.all' }));
  expect(screen.getByText('Mystery')).toBeInTheDocument();
});
it('gives the studio picker remaining height instead of a fixed box', () => {
  const source = readFileSync('app/dashboard/outfits/new/page.tsx', 'utf8');
  expect(source).toContain('heightClass="flex-1 min-h-0 basis-0"');
  expect(source).not.toContain('heightClass="h-[280px]"');
});

it.each([
  ['tops', 'dress'], ['tops', 'shirt'], ['layers', 'cardigan'], ['jackets', 'suit'],
  ['jackets', 'coat'], ['bottoms', 'pants'], ['bottoms', 'tights'],
  ['underwear', 'underwear'], ['socks', 'socks'], ['shoes', 'boots'],
  ['accessories', 'tie'], ['accessories', 'hat'],
])('maps %s to vocabulary type %s', (slot, type) => {
  mock.pages = [[{ id: 'match', name: 'Match', type: ` ${type.toUpperCase()} ` },
    { id: 'unknown', name: 'Unknown', type: '' }]];
  picker();
  fireEvent.click(screen.getByRole('button', { name: `slots.${slot}` }));
  expect(screen.getByText('Match')).toBeInTheDocument();
  expect(screen.queryByText('Unknown')).not.toBeInTheDocument();
});
it('renders an empty wardrobe without requesting another page', () => {
  mock.pages = [[]];
  picker();
  expect(screen.getByText('noItems')).toBeInTheDocument();
  expect(mock.page).toBe(1);
});
