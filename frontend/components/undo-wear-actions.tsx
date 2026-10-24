'use client';

import { useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import type { Outfit } from '@/lib/hooks/use-outfits';
import { useLogDayWear, useUndoDayWear, type WashedItem } from '@/lib/hooks/use-day-outfits';
import { useItemPages } from '@/lib/hooks/use-items';
import { useOccasionLabel } from '@/lib/hooks/use-translated-constants';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';

function ReplacementPicker({ choices, disabled, onChoose }: { choices: Outfit[]; disabled: boolean; onChoose: (choice: Outfit) => void }) {
  const t = useTranslations('outfits.day');
  const common = useTranslations('common');
  const occasionLabel = useOccasionLabel();
  const [pageCount, setPageCount] = useState(1);
  const wardrobe = useItemPages({ is_archived: false, status: 'ready' }, pageCount, 100);
  const readyIds = new Set(wardrobe.items.filter((item) => !item.is_archived && item.status === 'ready').map((item) => item.id));
  const availableChoices = choices.filter((choice) => choice.items.length > 0 && choice.items.every((item) => readyIds.has(item.id)));
  return <div className="max-h-[60vh] overflow-y-auto space-y-2">
    {wardrobe.isLoading && <p role="status">{common('loading')}</p>}
    {!wardrobe.isLoading && !availableChoices.length && <p>{t('empty')}</p>}
    {availableChoices.map((choice) => <Button key={choice.id} variant="outline" className="w-full" disabled={disabled || wardrobe.isFetching} onClick={() => onChoose(choice)}>{choice.name || occasionLabel(choice.occasion)}</Button>)}
    {wardrobe.hasMore && <Button variant="outline" disabled={wardrobe.isFetching} onClick={() => setPageCount((count) => count + 1)}>{common('loadMore')}</Button>}
  </div>;
}

export function UndoWearActions({ outfit, date, choices, disabled, onNotice }: {
  outfit: Outfit; date: string; choices: Outfit[]; disabled?: boolean; onNotice: (message: string) => void;
}) {
  const t = useTranslations('outfits.day');
  const common = useTranslations('common');
  const wardrobe = useTranslations('wardrobe.ai.queue');
  const undo = useUndoDayWear();
  const wear = useLogDayWear();
  const busy = useRef(false);
  const [pending, setPending] = useState(false);
  const [replaceOpen, setReplaceOpen] = useState(false);
  const [replacement, setReplacement] = useState<Outfit>();
  const [items, setItems] = useState<WashedItem[]>([]);
  const [checked, setChecked] = useState<string[]>([]);
  const unavailable = disabled || pending;

  const run = async (chosen?: Outfit, washedItemIds?: string[]) => {
    if (disabled || busy.current) return;
    busy.current = true;
    setPending(true);
    setReplacement(chosen);
    onNotice('');
    let undone = false;
    let stale = false;
    try {
      const result = await undo.mutateAsync({ outfitId: outfit.id, washedItemIds });
      if ('washedItems' in result) {
        setItems(result.washedItems);
        setChecked(result.washedItems.map((item) => item.id));
        setReplaceOpen(false);
        return;
      }
      undone = true;
      stale = result.learning_stale;
      setItems([]);
      setReplaceOpen(false);
      if (chosen) await wear.mutateAsync({ outfitId: chosen.id, date });
      onNotice(stale ? t('learningStale') : chosen ? t('logged') : t('undone'));
    } catch {
      setItems([]);
      setReplaceOpen(false);
      onNotice(undone ? t(stale ? 'replaceFailedStale' : 'replaceFailed') : t('failed'));
    } finally {
      busy.current = false;
      setPending(false);
    }
  };

  return <>
    <div className="flex items-center gap-1">
      <Button size="sm" className="px-2 text-xs" disabled={unavailable} onClick={() => run()}>{t('undo')}</Button>
      <Button size="sm" className="px-2 text-xs" variant="outline" disabled={unavailable} onClick={() => setReplaceOpen(true)}>{t('replace')}</Button>
    </div>
    <Dialog open={replaceOpen} onOpenChange={(open) => { if (!pending) setReplaceOpen(open); }}><DialogContent>
      <DialogHeader><DialogTitle>{t('replace')}</DialogTitle></DialogHeader>
      {replaceOpen && <ReplacementPicker choices={choices} disabled={!!unavailable} onChoose={(choice) => run(choice)} />}
    </DialogContent></Dialog>
    <Dialog open={items.length > 0} onOpenChange={(open) => { if (!open && !pending) { setItems([]); setReplacement(undefined); } }}><DialogContent>
      <DialogHeader><DialogTitle>{t('washedSince')}</DialogTitle></DialogHeader>
      {items.map((item) => <label key={item.id} className="flex items-center gap-2">
        <input type="checkbox" disabled={pending} checked={checked.includes(item.id)} onChange={(event) => setChecked((ids) => event.target.checked ? [...ids, item.id] : ids.filter((id) => id !== item.id))} />
        {item.name || wardrobe('unnamedItem')}
      </label>)}
      <Button disabled={unavailable} onClick={() => run(replacement, checked)}>{common('confirm')}</Button>
    </DialogContent></Dialog>
  </>;
}
