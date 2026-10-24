'use client';

import { useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import { ApiError } from '@/lib/api';
import type { Outfit } from '@/lib/hooks/use-outfits';
import { useLogDayWear, useUndoDayWear } from '@/lib/hooks/use-day-outfits';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';

type WashedItem = { id: string; name: string | null; washed_at: string };

function washedItems(error: unknown): WashedItem[] | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = (error.data as { detail?: { error_code?: string; items?: unknown } } | null)?.detail;
  if (detail?.error_code !== 'WASH_SINCE_WEAR' || !Array.isArray(detail.items) || !detail.items.length) return null;
  return detail.items.every((item) => item && typeof item.id === 'string' && (item.name === null || typeof item.name === 'string') && typeof item.washed_at === 'string')
    ? detail.items : null;
}

export function UndoWearActions({ outfit, date, choices, disabled, onNotice }: {
  outfit: Outfit; date: string; choices: Outfit[]; disabled?: boolean; onNotice: (message: string) => void;
}) {
  const t = useTranslations('outfits.day');
  const common = useTranslations('common');
  const wardrobe = useTranslations('wardrobe.ai.queue');
  const undo = useUndoDayWear();
  const wear = useLogDayWear(true);
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
      undone = true;
      stale = result.learning_stale;
      setItems([]);
      setReplaceOpen(false);
      if (chosen) await wear.mutateAsync({ outfitId: chosen.id, date });
      onNotice(stale ? t('learningStale') : chosen ? t('logged') : t('undone'));
    } catch (error) {
      const washed = undone ? null : washedItems(error);
      if (washed) {
        setItems(washed);
        setChecked(washed.map((item) => item.id));
        setReplaceOpen(false);
      } else {
        onNotice(undone ? t('replaceFailed') : t('failed'));
        if (stale) onNotice(`${t('replaceFailed')} ${t('learningStale')}`);
      }
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
      <div className="max-h-[60vh] overflow-y-auto space-y-2">
        {!choices.length && <p>{t('empty')}</p>}
        {choices.map((choice) => <Button key={choice.id} variant="outline" className="w-full" disabled={unavailable || !choice.items.length} onClick={() => run(choice)}>{choice.name || choice.occasion}</Button>)}
      </div>
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
