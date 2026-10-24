'use client';

import { useEffect, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import type { Outfit } from '@/lib/hooks/use-outfits';
import { useLogDayWear, useUndoDayWear, type WashedItem } from '@/lib/hooks/use-day-outfits';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { ItemListResponse } from '@/lib/types';
import { queryKeys } from '@/lib/hooks/query-keys';
import { useOccasionLabel } from '@/lib/hooks/use-translated-constants';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';

function ReplacementPicker({ choices, disabled, onChoose }: { choices: Outfit[]; disabled: boolean; onChoose: (choice: Outfit) => void }) {
  const t = useTranslations('outfits.day');
  const common = useTranslations('common');
  const occasionLabel = useOccasionLabel();
  const queryClient = useQueryClient();
  const [wardrobe, setWardrobe] = useState<{ items: ItemListResponse['items']; loading: boolean; failed: boolean }>({ items: [], loading: true, failed: false });
  useEffect(() => {
    let active = true;
    const load = async () => {
      const items: ItemListResponse['items'] = [];
      let page = 1;
      try {
        while (active) {
          const result = await queryClient.fetchQuery({
            queryKey: queryKeys.items.list({ is_archived: false, status: 'ready' }, page, 100),
            staleTime: 0,
            retry: false,
            queryFn: () => api.get<ItemListResponse>('/items', {
              params: { is_archived: 'false', status: 'ready', page: String(page), page_size: '100' },
            }),
          });
          items.push(...result.items);
          if (!result.has_more) {
            if (active) setWardrobe({ items, loading: false, failed: false });
            return;
          }
          if (!result.items.length) throw new Error('Incomplete item page');
          page += 1;
        }
      } catch {
        if (active) setWardrobe({ items: [], loading: false, failed: true });
      }
    };
    void load();
    return () => { active = false; };
  }, [queryClient]);
  const loading = wardrobe.loading;
  const readyIds = new Set(wardrobe.items.filter((item) => !item.is_archived && item.status === 'ready').map((item) => item.id));
  const availableChoices = choices.filter((choice) => choice.items.length > 0 && choice.items.every((item) => readyIds.has(item.id)));
  return <div className="max-h-[60vh] overflow-y-auto space-y-2">
    {loading && <p role="status">{common('loading')}</p>}
    {!loading && wardrobe.failed && <p role="alert">{t('failed')}</p>}
    {!loading && !wardrobe.failed && !availableChoices.length && <p>{t('empty')}</p>}
    {!loading && !wardrobe.failed && availableChoices.map((choice) => <Button key={choice.id} variant="outline" className="w-full" disabled={disabled} onClick={() => onChoose(choice)}>{choice.name || occasionLabel(choice.occasion)}</Button>)}
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
