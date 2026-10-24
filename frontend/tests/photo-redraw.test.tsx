import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PhotoRedrawControls } from '@/components/photo-redraw-controls';
import { Item } from '@/lib/types';
import { ApiError } from '@/lib/api';

const mocks = vi.hoisted(() => ({
  redraw: vi.fn(), choose: vi.fn(), refresh: vi.fn(), enabled: true as boolean | undefined,
}));
vi.mock('@/lib/hooks/use-features', () => ({ useFeatures: () => ({ data: { photo_redraw: mocks.enabled } }) }));
vi.mock('@/lib/hooks/use-items', () => ({
  useRedrawPhoto: () => ({ mutateAsync: mocks.redraw, isPending: false }),
  useChoosePhoto: () => ({ mutateAsync: mocks.choose, isPending: false }),
  useItem: () => ({ refetch: mocks.refresh }),
}));
const item = { id: 'one', status: 'ready' } as Item;
function show(overrides: Partial<Item> = {}) {
  return render(<PhotoRedrawControls item={{ ...item, ...overrides }} />);
}
afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  mocks.enabled = true;
  mocks.redraw.mockResolvedValue(item);
  mocks.choose.mockResolvedValue(item);
  mocks.refresh.mockResolvedValue({ data: item });
});
describe('photo redraw controls', () => {
  it.each([false, undefined])('hides paid actions when availability is %s', (enabled) => {
    mocks.enabled = enabled;
    show();
    expect(screen.queryByRole('button', { name: 'redraw' })).toBeNull();
  });
  it('requires confirmation before the first paid request', async () => {
    show();
    fireEvent.click(screen.getByRole('button', { name: 'redraw' }));
    expect(mocks.redraw).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'confirm' }));
    await waitFor(() => expect(mocks.redraw).toHaveBeenCalledWith({ id: 'one', retry: false }));
  });
  it('does not charge after cancelling', () => {
    show();
    fireEvent.click(screen.getByRole('button', { name: 'redraw' }));
    fireEvent.click(screen.getByRole('button', { name: 'cancel' }));
    expect(mocks.redraw).not.toHaveBeenCalled();
  });
  it('requires explicit confirmation for a paid retry', async () => {
    show({ photo_redraw: { status: 'failed', paid: true } });
    fireEvent.click(screen.getByRole('button', { name: 'retry' }));
    expect(screen.getByText('paidRetry')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'confirm' }));
    await waitFor(() => expect(mocks.redraw).toHaveBeenCalledWith({ id: 'one', retry: true }));
  });
  it('offers free switching even when generation is disabled', async () => {
    mocks.enabled = false;
    show({ photo_redraw: { status: 'generated', selected: 'redrawn' }, photo_choices: { original: '/original', redrawn: '/redrawn' } });
    fireEvent.click(screen.getByRole('button', { name: 'redrawn' }));
    await waitFor(() => expect(mocks.choose).toHaveBeenCalledWith({ id: 'one', choice: 'original' }));
    expect(mocks.redraw).not.toHaveBeenCalled();
  });
  it('refreshes on conflict without another paid request', async () => {
    mocks.redraw.mockRejectedValue(new ApiError('Photo redraw already requested or item busy', 409, {}));
    show();
    fireEvent.click(screen.getByRole('button', { name: 'redraw' }));
    fireEvent.click(screen.getByRole('button', { name: 'confirm' }));
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());
    expect(mocks.redraw).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('conflict')).toBeInTheDocument();
  });
  it.each(['generating', 'skipped', 'color_drift', 'source_changed'])('does not offer another paid request after %s', (status) => {
    show({ photo_redraw: { status } });
    expect(screen.queryByRole('button', { name: 'redraw' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'retry' })).toBeNull();
    expect(screen.getByText(status)).toBeInTheDocument();
  });
  it('resets the confirmation and error when another item opens', () => {
    const view = show();
    fireEvent.click(screen.getByRole('button', { name: 'redraw' }));
    view.rerender(<PhotoRedrawControls item={{ ...item, id: 'two' }} />);
    expect(screen.queryByRole('button', { name: 'confirm' })).toBeNull();
  });
});
