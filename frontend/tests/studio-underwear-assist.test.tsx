import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DetailsPanel } from '@/components/studio/details-panel';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import type { StudioItem } from '@/lib/studio/editor-state';

vi.mock('@/lib/api', () => ({ api: { post: vi.fn() }, getErrorMessage: vi.fn() }));
vi.mock('sonner', () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() } }));
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }));
vi.mock('@/lib/hooks/use-translated-constants', () => ({ useTypeLabel: () => (type: string) => type, useOccasionOptions: () => [] }));

const item = (id: string, type: string): StudioItem => ({
  id, type, name: type, thumbnail_url: null, image_url: null, primary_color: null,
});
beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

function panel(items: StudioItem[], occasion: string | null = 'casual') {
  const onAiMerge = vi.fn();
  render(<DetailsPanel items={items} name="Outfit" occasion={occasion}
    onNameChange={vi.fn()} onOccasionChange={vi.fn()} onAiMerge={onAiMerge} />);
  return onAiMerge;
}

it('sends outfit garments and keeps canvas underwear after AI assist', async () => {
  const underwear = item('underwear', 'underwear');
  const shirt = item('shirt', 'shirt');
  const shorts = item('shorts', 'shorts');
  vi.mocked(api.post).mockResolvedValue({ items: [shirt, shorts] });
  const onAiMerge = panel([shirt, underwear]);
  fireEvent.click(screen.getByRole('button', { name: 'letAiFinish' }));
  await waitFor(() => expect(onAiMerge).toHaveBeenCalled());
  expect(api.post).toHaveBeenCalledWith('/outfits/suggest', {
    occasion: 'casual', include_items: ['shirt'],
  });
  expect(onAiMerge.mock.calls[0][0]).toEqual(expect.arrayContaining([shirt, shorts, underwear]));
  expect(onAiMerge.mock.calls[0][0]).toHaveLength(3);
});

it('shows the existing add-items error without a request for underwear alone', () => {
  panel([item('underwear', 'underwear')]);
  fireEvent.click(screen.getByRole('button', { name: 'letAiFinish' }));
  expect(toast.error).toHaveBeenCalledWith('aiAssistError');
  expect(api.post).not.toHaveBeenCalled();
});

it.each([{ items: [] }, { items: [item('shirt', 'shirt')] }])('does not request suggestions without items or occasion', ({ items }) => {
  panel(items, null);
  expect(screen.getByRole('button', { name: 'letAiFinish' })).toBeDisabled();
  expect(api.post).not.toHaveBeenCalled();
});
