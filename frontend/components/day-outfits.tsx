'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useDayOutfits, useLogDayWear, loggedOutfitName } from '@/lib/hooks/use-day-outfits';
import { useInfiniteOutfits, useRejectOutfit, type Outfit } from '@/lib/hooks/use-outfits';
import { useItemPages } from '@/lib/hooks/use-items';
import { useUserToday } from '@/lib/hooks/use-user';
import { usePreferences } from '@/lib/hooks/use-preferences';
import { useTypeLabel } from '@/lib/hooks/use-translated-constants';
import { invalidateOutfitCaches } from '@/lib/hooks/cache-invalidation';
import { slotsForType } from '@/lib/studio/canonical-order';
import type { Item } from '@/lib/types';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { OutfitCard } from '@/components/outfits/outfit-card';

export function shiftDay(date: string, offset: number) {
  const parsed = new Date(`${date}T12:00:00Z`);
  parsed.setUTCDate(parsed.getUTCDate() + offset);
  return parsed.toISOString().slice(0, 10);
}

export function canSwap(originalType: string, item: Item, selectedIds: string[]) {
  const slots = slotsForType(originalType);
  const candidateSlots = slotsForType(item.type);
  const sameSlot = slots.length > 0
    ? slots.length === candidateSlots.length && slots.every((slot) => candidateSlots.includes(slot))
    : originalType === item.type;
  return sameSlot && item.status === 'ready' && !item.is_archived && !item.needs_wash && !selectedIds.includes(item.id);
}

function SwapPicker({ outfit, onConfirm }: { outfit: Outfit; onConfirm: (ids: string[]) => void }) {
  const t = useTranslations('outfits.day');
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
    {wardrobe.isLoading && <p role="status">{t('loading')}</p>}
    {wardrobe.hasMore && <Button variant="outline" disabled={wardrobe.isFetching} onClick={() => setPageCount((count) => count + 1)}>{t('more')}</Button>}
    <Button disabled={wardrobe.isFetching || ids.every((id, index) => id === outfit.items[index].id)} onClick={() => onConfirm(ids)}>{t('wore')}</Button>
  </div>;
}

export function WearActions({ outfit, date, disabled = false }: { outfit: Outfit; date: string; disabled?: boolean }) {
  const t = useTranslations('outfits.day');
  const today = useUserToday();
  const wear = useLogDayWear();
  const [swap, setSwap] = useState(false);
  const [notice, setNotice] = useState('');
  const unavailable = disabled || date > today() || wear.isPending || !outfit.items.length;
  const log = async (itemIds?: string[]) => {
    if (unavailable) return;
    setNotice('');
    try {
      await wear.mutateAsync({ outfitId: outfit.id, date, itemIds });
      setSwap(false);
      setNotice(t('logged'));
    } catch (error) {
      try {
        const name = await loggedOutfitName(error);
        setNotice(name ? t('alreadyLogged', { name }) : t('failed'));
      } catch { setNotice(t('failed')); }
    }
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

export function DayOutfits() {
  const t = useTranslations('outfits.day');
  const today = useUserToday()();
  const [selectedDate, setDate] = useState<string | null>(null);
  const date = selectedDate ?? today;
  const day = useDayOutfits(date);
  const saved = useInfiniteOutfits({ source: 'manual', is_lookbook: true });
  const past = useInfiniteOutfits({ worn: true, date_to: shiftDay(date, -1) });
  const reject = useRejectOutfit();
  const { data: preferences } = usePreferences();
  const queryClient = useQueryClient();
  const generate = useMutation({
    mutationFn: () => api.post<Outfit[]>('/outfits/suggest-options', { date, occasion: preferences?.default_occasion || 'casual' }),
    onSuccess: () => invalidateOutfitCaches(queryClient),
  });
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
      <Button variant="outline" onClick={() => setDate(shiftDay(date, -1))} aria-label={t('previous')}>←</Button>
      <span>{date}</span>
      <Button variant="outline" disabled={date >= shiftDay(today, 1)} onClick={() => setDate(shiftDay(date, 1))} aria-label={t('next')}>→</Button>
      <Button variant="ghost" onClick={() => setDate(null)}>{t('today')}</Button>
    </div>
    {date > today && <p>{t('preview')}</p>}
    {date >= today && <Button disabled={logged || generate.isPending || day.isLoading || day.isError} onClick={() => generate.mutate()}>{t('generate')}</Button>}
    {generate.isError && <p role="alert">{t('failed')}</p>}
    {sections.map(({ title, query }) => <div key={title} className="space-y-3">
      <h3 className="font-semibold">{title}</h3>
      {query.isLoading && <p role="status">{t('loading')}</p>}
      {query.isError && <p role="alert">{t('failed')}</p>}
      {query.data?.pages[0]?.total === 0 && <p>{t('empty')}</p>}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {query.data?.pages.flatMap((page) => page.outfits).filter((outfit) => {
          if (seen.has(outfit.id) || outfit.status === 'rejected' || outfit.status === 'expired') return false;
          seen.add(outfit.id);
          return true;
        }).map((outfit) => <Card key={outfit.id}><CardContent className="p-3 space-y-3">
          <OutfitCard outfit={outfit} />
          {outfit.feedback?.worn_at === date ? <p>{t('logged')}</p> : <WearActions outfit={outfit} date={date} />}
          {!outfit.feedback?.worn_at && ['pending', 'sent', 'viewed'].includes(outfit.status) && <Button variant="ghost" disabled={reject.isPending} onClick={() => reject.mutate(outfit.id)}>{t('dismiss')}</Button>}
        </CardContent></Card>)}
      </div>
      {query.hasNextPage && <Button variant="outline" disabled={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>{t('more')}</Button>}
    </div>)}
    {reject.isError && <p role="alert">{t('failed')}</p>}
  </section>;
}
