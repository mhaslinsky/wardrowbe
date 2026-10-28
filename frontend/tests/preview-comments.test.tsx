import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { OutfitPreview } from '@/components/outfit-preview';

const state = vi.hoisted(() => ({ locale: 'en', saveComment: vi.fn().mockResolvedValue(true), draw: vi.fn() }));
const catalogs = import.meta.glob('../messages/*/outfits.json', { eager: true, import: 'default' }) as Record<string, { preview: Record<string, string> }>;
vi.mock('next-intl', () => ({ useTranslations: (namespace: string) => (key: string) => namespace === 'common' ? key : catalogs[`../messages/${state.locale}/outfits.json`].preview[key] }));
vi.mock('@/lib/hooks/use-outfit-preview', () => ({ useOutfitPreview: () => ({
  preview: { image_url: '/preview.jpg', created_at: '2026-10-28', comment: 'Good color, wrong fit' },
  pending: false, saveComment: state.saveComment, draw: state.draw,
}) }));

beforeEach(() => { state.locale = 'en'; state.saveComment.mockClear(); state.draw.mockClear(); });

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

it.each(labels)('renders Comment controls in %s', (locale, label, placeholder) => {
  state.locale = locale;
  const messages = catalogs[`../messages/${locale}/outfits.json`].preview;
  render(<OutfitPreview outfitId="one" cardImageIsPreview={false} />);
  const button = screen.getByRole('button', { name: messages.comment });
  expect(button).not.toHaveAttribute('aria-pressed');
  expect(screen.getByText('Good color, wrong fit')).toHaveClass('truncate', 'text-muted-foreground');
  fireEvent.click(button);
  expect(screen.getByRole('heading', { name: messages.comment })).toBeVisible();
  expect(within(screen.getByRole('dialog')).getByText(label, { selector: 'span' })).toBeVisible();
  expect(screen.getByRole('textbox', { name: label })).toHaveAttribute('placeholder', placeholder);
});

it('saves mixed feedback unchanged and resets to the saved note on reopening', async () => {
  render(<OutfitPreview outfitId="one" cardImageIsPreview={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'Comment' }));
  const input = screen.getByRole('textbox', { name: 'Comments' });
  expect(input).toHaveValue('Good color, wrong fit');
  fireEvent.change(input, { target: { value: '  Great color, sleeves too short  ' } });
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
  expect(state.saveComment).toHaveBeenCalledWith('  Great color, sleeves too short  ', '2026-10-28');
  fireEvent.click(screen.getByRole('button', { name: 'Comment' }));
  expect(screen.getByRole('textbox', { name: 'Comments' })).toHaveValue('Good color, wrong fit');
});

it('keeps the Redraw instruction label and no comments placeholder', () => {
  render(<OutfitPreview outfitId="one" cardImageIsPreview={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'Redraw' }));
  expect(screen.getByRole('textbox', { name: 'What should change?' })).not.toHaveAttribute('placeholder');
});

it('allows clearing a saved comment', () => {
  render(<OutfitPreview outfitId="one" cardImageIsPreview={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'Comment' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '' } });
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
  expect(state.saveComment).toHaveBeenCalledWith('', '2026-10-28');
});
