import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { OutfitPreview } from '@/components/outfit-preview';

const state = vi.hoisted(() => ({ locale: 'en', like: vi.fn().mockResolvedValue(true), draw: vi.fn() }));
const catalogs = import.meta.glob('../messages/*/outfits.json', { eager: true, import: 'default' }) as Record<string, { preview: Record<string, string> }>;
vi.mock('next-intl', () => ({ useTranslations: (namespace: string) => (key: string) => namespace === 'common' ? key : catalogs[`../messages/${state.locale}/outfits.json`].preview[key] }));
vi.mock('@/lib/hooks/use-outfit-preview', () => ({ useOutfitPreview: () => ({
  preview: { image_url: '/preview.jpg', created_at: '2026-10-28', like_note: 'Good color, wrong fit' },
  pending: false, like: state.like, draw: state.draw,
}) }));

beforeEach(() => { state.locale = 'en'; state.like.mockClear(); state.draw.mockClear(); });

const labels = [
  ['en', 'Comments', "What worked, what didn't"],
  ['de', 'Kommentare', 'Was gut war und was nicht'],
  ['fr', 'Commentaires', 'Ce qui a fonctionné ou non'],
  ['it', 'Commenti', 'Cosa ha funzionato e cosa no'],
  ['ja', 'コメント', '良かった点、良くなかった点'],
  ['ko', '의견', '좋았던 점과 아쉬웠던 점'],
  ['zh-CN', '评论', '哪些地方好，哪些地方不好'],
  ['zh-TW', '評論', '哪些地方好，哪些地方不好'],
];

it.each(labels)('renders generic Like comments in %s', (locale, label, placeholder) => {
  state.locale = locale;
  const messages = catalogs[`../messages/${locale}/outfits.json`].preview;
  render(<OutfitPreview outfitId="one" cardImageIsPreview={false} />);
  fireEvent.click(screen.getByRole('button', { name: messages.like }));
  expect(screen.getByText(label)).toBeVisible();
  expect(screen.getByRole('textbox', { name: label })).toHaveAttribute('placeholder', placeholder);
});

it('saves mixed feedback unchanged and resets to the saved note on reopening', async () => {
  render(<OutfitPreview outfitId="one" cardImageIsPreview={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'Like' }));
  const input = screen.getByRole('textbox', { name: 'Comments' });
  expect(input).toHaveValue('Good color, wrong fit');
  fireEvent.change(input, { target: { value: '  Great color, sleeves too short  ' } });
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Like' }));
  expect(state.like).toHaveBeenCalledWith('  Great color, sleeves too short  ', '2026-10-28');
  fireEvent.click(screen.getByRole('button', { name: 'Like' }));
  expect(screen.getByRole('textbox', { name: 'Comments' })).toHaveValue('Good color, wrong fit');
});

it('keeps the Redraw instruction label and no comments placeholder', () => {
  render(<OutfitPreview outfitId="one" cardImageIsPreview={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'Redraw' }));
  expect(screen.getByRole('textbox', { name: 'What should change?' })).not.toHaveAttribute('placeholder');
});
