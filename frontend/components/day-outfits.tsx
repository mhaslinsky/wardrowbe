'use client';

import { useState, useEffect, useRef } from 'react';
import { useTranslations } from 'next-intl';
import { useDayOutfits, useDaySelection, useGenerateDayOptions, useLogDayWear, loggedOutfitName } from '@/lib/hooks/use-day-outfits';
import { useInfiniteOutfits, useRejectOutfit, type Outfit } from '@/lib/hooks/use-outfits';
import { useItemPages } from '@/lib/hooks/use-items';
import { useUserToday, useUserProfile } from '@/lib/hooks/use-user';
import { usePreferences } from '@/lib/hooks/use-preferences';
import { useTypeLabel } from '@/lib/hooks/use-translated-constants';
import { slotsForType } from '@/lib/studio/canonical-order';
import type { Item } from '@/lib/types';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { OutfitCard } from '@/components/outfits/outfit-card';

function shiftDay(date: string, offset: number) {
  const parsed = new Date(`${date}T12:00:00Z`);
  parsed.setUTCDate(parsed.getUTCDate() + offset);
  return parsed.toISOString().slice(0, 10);
}

function canSwap(originalType: string, item: Item, selectedIds: string[]) {
  const slots = slotsForType(originalType);
  const candidateSlots = slotsForType(item.type);
  const sameSlot = slots.length > 0
    ? slots.length === candidateSlots.length && slots.every((slot) => candidateSlots.includes(slot))
    : originalType === item.type;
  return sameSlot && item.status === 'ready' && !item.is_archived && !item.needs_wash && !selectedIds.includes(item.id);
}

function SwapPicker({ outfit, onConfirm }: { outfit: Outfit; onConfirm: (ids: string[]) => void }) {
  const t = useTranslations('outfits.day');
  const common = useTranslations('common');
  const typeLabel = useTypeLabel();
  const [ids, setIds] = useState(outfit.items.map((item) => item.id));
  const [pageCount, setPageCount] = useState(1);
  const wardrobe = useItemPages({ needs_wash: false, is_archived: false }, pageCount, 100);
  return <div className="space-y-3">
    {outfit.items.map((piece, index) => <label key={piece.id} className="block space-y-1">
      <span>{piece.name || typeLabel(piece.type)}</span>
      <select className="block w-full rounded border bg-background p-2" value={ids[index]} onChange={(event) => {
        const nextIds = [...ids];
        nextIds[index] = event.target.value;
        setIds(nextIds);
      }}>
        <option value={piece.id}>{piece.name || typeLabel(piece.type)}</option>
        {wardrobe.items.filter((item) => canSwap(piece.type, item, ids.filter((_, position) => position !== index)))
          .filter((item) => item.id !== piece.id).map((item) => <option key={item.id} value={item.id}>{item.name || typeLabel(item.type)}</option>)}
      </select>
    </label>)}
    {wardrobe.isLoading && <p role="status">{common('loading')}</p>}
    {wardrobe.hasMore && <Button variant="outline" disabled={wardrobe.isFetching} onClick={() => setPageCount((count) => count + 1)}>{common('loadMore')}</Button>}
    <Button disabled={wardrobe.isFetching || ids.every((id, index) => id === outfit.items[index].id)} onClick={() => onConfirm(ids)}>{t('wore')}</Button>
  </div>;
}

interface WearActionsProps {
  outfit: Outfit;
  date: string;
  disabled?: boolean;
  onSuccess?: (outfit: Outfit) => void;
  onPendingChange?: (pending: boolean) => void;
}

export function WearActions(props: WearActionsProps) {
  return <DayWearActions key={`${props.outfit.id}:${props.date}`} {...props} />;
}

function DayWearActions({ outfit, date, disabled = false, onSuccess, onPendingChange }: WearActionsProps) {
  const t = useTranslations('outfits.day');
  const today = useUserToday();
  const { data: profile } = useUserProfile();
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  const wear = useLogDayWear();
  const [swap, setSwap] = useState(false);
  const [notice, setNotice] = useState('');
  const unavailable = !profile || disabled || date > today() || wear.isPending || !outfit.items.length;
  const log = async (itemIds?: string[]) => {
    if (unavailable) return;
    setNotice('');
    onPendingChange?.(true);
    try {
      const result = await wear.mutateAsync({ outfitId: outfit.id, date, itemIds });
      if (!active.current) return;
      setSwap(false);
      setNotice(t('logged'));
      onSuccess?.(result);
    } catch (error) {
      if (!active.current) return;
      try {
        const name = await loggedOutfitName(error);
        if (!active.current) return;
        setNotice(name ? t('alreadyLogged', { name }) : t('failed'));
      } catch { if (active.current) setNotice(t('failed')); }
    } finally { onPendingChange?.(false); }
  };
  return <div className="space-y-2">
    <div className="flex flex-wrap gap-2">
      <Button disabled={unavailable} onClick={() => log()}>{t('wore')}</Button>
      <Button variant="outline" disabled={unavailable} onClick={() => setSwap(true)}>{t('swap')}</Button>
    </div>
    {notice && <p role="status">{notice}</p>}
    <Dialog open={swap} onOpenChange={setSwap}><DialogContent>
      <DialogHeader><DialogTitle>{t('swap')}</DialogTitle></DialogHeader>
      {swap && <SwapPicker key={outfit.id} outfit={outfit} onConfirm={(ids) => { if (!wear.isPending) log(ids); }} />}
    </DialogContent></Dialog>
  </div>;
}

export function DayOutfits({ date: controlledDate, onDateChange }: { date?: string; onDateChange?: (date: string | null) => void } = {}) {
  const t = useTranslations('outfits.day');
  const common = useTranslations('common');
  const selection = useDaySelection();
  const { today, ready } = selection;
  const date = controlledDate ?? selection.date;
  const setDate = onDateChange ?? selection.setDate;
  const day = useDayOutfits(date);
  const saved = useInfiniteOutfits({ source: 'manual', is_lookbook: true });
  const past = useInfiniteOutfits({ worn: true, date_to: shiftDay(date, -1) });
  const reject = useRejectOutfit();
  const { data: preferences } = usePreferences();
  const generate = useGenerateDayOptions();
  const dayOutfits = day.data?.pages.flatMap((page) => page.outfits) ?? [];
  const logged = dayOutfits.some((outfit) => !!outfit.feedback?.worn_at);
  const sections = [
    { title: t('options'), query: day },
    { title: t('saved'), query: saved },
    { title: t('past'), query: past },
  ];
  const seen = new Set<string>();
  return <section className="space-y-4">
    <div className="flex flex-wrap items-center gap-3">
      <h2 className="text-xl font-semibold">{t('title')}</h2>
      <Button variant="outline" disabled={!ready} onClick={() => setDate(shiftDay(date, -1))} aria-label={t('previous')}>←</Button>
      <span>{date}</span>
      <Button variant="outline" disabled={!ready || date >= shiftDay(today, 1)} onClick={() => setDate(shiftDay(date, 1))} aria-label={t('next')}>→</Button>
      <Button variant="ghost" disabled={!ready} onClick={() => setDate(null)}>{t('today')}</Button>
    </div>
    {date > today && <p>{t('preview')}</p>}
    {date >= today && <Button disabled={!ready || logged || generate.isPending || day.isLoading || day.isError} onClick={() => generate.mutate({ date, occasion: preferences?.default_occasion || 'casual' })}>{t('generate')}</Button>}
    {generate.isError && <p role="alert">{t('generateFailed')}</p>}
    {sections.map(({ title, query }) => <div key={title} className="space-y-3">
      <h3 className="font-semibold">{title}</h3>
      {query.isLoading && <p role="status">{common('loading')}</p>}
      {query.isError && <p role="alert">{t('failed')}</p>}
      {query.data?.pages[0]?.total === 0 && <p>{t('empty')}</p>}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {query.data?.pages.flatMap((page) => page.outfits).filter((outfit) => {
          if (seen.has(outfit.id) || outfit.status === 'rejected' || outfit.status === 'expired') return false;
          seen.add(outfit.id);
          return true;
        }).map((outfit) => <Card key={outfit.id}><CardContent className="p-3 space-y-3">
          <OutfitCard outfit={outfit} />
          {outfit.feedback?.worn_at === date ? <p>{t('logged')}</p> : <WearActions outfit={outfit} date={date} disabled={!ready} />}
          {!outfit.feedback?.worn_at && ['pending', 'sent', 'viewed'].includes(outfit.status) && <Button variant="ghost" disabled={reject.isPending} onClick={() => reject.mutate(outfit.id)}>{t('dismiss')}</Button>}
        </CardContent></Card>)}
      </div>
      {query.hasNextPage && <Button variant="outline" disabled={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>{common('loadMore')}</Button>}
    </div>)}
    {reject.isError && <p role="alert">{t('failed')}</p>}
  </section>;
}
