'use client';

import { OutfitPreview } from '@/components/outfit-preview';
import { useState, useEffect, useRef } from 'react';
import { CalendarCheck } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
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
import { UndoWearActions } from '@/components/undo-wear-actions';
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
  const loading = wardrobe.isLoading || wardrobe.isFetching || wardrobe.hasMore;
  // A partial wardrobe can omit the piece the user actually wore.
  useEffect(() => {
    if (wardrobe.hasMore && !wardrobe.isFetching && !wardrobe.isError) {
      setPageCount((count) => count + 1);
    }
  }, [wardrobe.hasMore, wardrobe.isFetching, wardrobe.isError]);
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
          .filter((item) => item.id !== piece.id)
          .sort((first, second) => (first.name || typeLabel(first.type)).localeCompare(second.name || typeLabel(second.type)))
          .map((item) => <option key={item.id} value={item.id}>{item.name || typeLabel(item.type)}</option>)}
      </select>
    </label>)}
    {wardrobe.isError ? <p role="alert">{common('unexpectedError')}</p> : loading && <p role="status">{common('loading')}</p>}
    <Button disabled={loading || wardrobe.isError || ids.every((id, index) => id === outfit.items[index].id)} onClick={() => onConfirm(ids)}>{t('wore')}</Button>
  </div>;
}

interface WearActionsProps {
  outfit: Outfit;
  date: string;
  disabled?: boolean;
  dismissAction?: React.ReactNode;
  onSuccess?: (outfit: Outfit) => void;
  onPendingChange?: (pending: boolean) => void;
}

export function WearActions(props: WearActionsProps) {
  return <DayWearActions key={`${props.outfit.id}:${props.date}`} {...props} />;
}

function DayWearActions({ outfit, date, disabled = false, onSuccess, onPendingChange, dismissAction }: WearActionsProps) {
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
    <div className="flex items-center gap-1">
      <Button size="sm" className="px-2 text-xs" disabled={unavailable} onClick={() => log()}>{t('wore')}</Button>
      <Button size="sm" className="px-2 text-xs" variant="outline" disabled={unavailable} onClick={() => setSwap(true)}>{t('swap')}</Button>
      {dismissAction}
    </div>
    {notice && <p role="status">{notice}</p>}
    <Dialog open={swap} onOpenChange={setSwap}><DialogContent>
      <DialogHeader><DialogTitle>{t('swap')}</DialogTitle></DialogHeader>
      {swap && <SwapPicker key={outfit.id} outfit={outfit} onConfirm={(ids) => { if (!wear.isPending) log(ids); }} />}
    </DialogContent></Dialog>
  </div>;
}

function WornOutfits({ outfits, date, choices, disabled, onNotice }: { outfits: Outfit[]; date: string; choices: Outfit[]; disabled: boolean; onNotice: (message: string) => void }) {
  const t = useTranslations('outfits.day');
  return <div className="space-y-3">
    <h3 className="font-semibold">{t('worn')}</h3>
    <div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(min(100%,18rem),1fr))]">
      {outfits.map((outfit) => <Card key={outfit.id}><CardContent className="p-3 space-y-3">
        <OutfitPreview key={outfit.id} outfitId={outfit.id} cardImageUrl={outfit.card_image_url} cardImageIsPreview={outfit.card_image_choice === 'preview'}><OutfitCard outfit={outfit} hideWashWarnings /></OutfitPreview>
        <UndoWearActions outfit={outfit} date={date} choices={choices} disabled={disabled} onNotice={onNotice} />
      </CardContent></Card>)}
    </div>
  </div>;
}

export function DayOutfits({ date: controlledDate, onDateChange }: { date?: string; onDateChange?: (date: string | null) => void } = {}) {
  const t = useTranslations('outfits.day');
  const common = useTranslations('common');
  const [notice, setNotice] = useState({ date: '', message: '' });
  const locale = useLocale();
  const selection = useDaySelection();
  const { today, ready } = selection;
  const date = controlledDate ?? selection.date;
  const setDate = onDateChange ?? selection.setDate;
  const day = useDayOutfits(date);
  const saved = useInfiniteOutfits({ source: 'manual', is_lookbook: true, owned_only: true });
  const past = useInfiniteOutfits({ worn: true, date_to: shiftDay(date, -1) });
  const reject = useRejectOutfit();
  const { data: preferences } = usePreferences();
  const generate = useGenerateDayOptions();
  const dayOutfits = day.data?.pages.flatMap((page) => page.outfits) ?? [];
  const wornOutfits = dayOutfits.filter((outfit) => outfit.feedback?.worn_at === date);
  const logged = wornOutfits.length > 0;
  const [year, month, dayOfMonth] = date.split('-').map(Number);
  const dateLabel = date === today ? t('today') : date === shiftDay(today, 1) ? t('tomorrow')
    : new Intl.DateTimeFormat(locale, { weekday: 'short', month: 'short', day: 'numeric' }).format(new Date(year, month - 1, dayOfMonth));
  const seen = new Set(wornOutfits.map((outfit) => outfit.id));
  const sections = [
    { title: t('options'), query: day },
    { title: t('saved'), query: saved },
    { title: t('past'), query: past },
  ].map((section) => ({
    ...section,
    outfits: (section.query.data?.pages.flatMap((page) => page.outfits) ?? []).filter((outfit) => {
      if (seen.has(outfit.id) || outfit.status === 'rejected' || outfit.status === 'expired') return false;
      seen.add(outfit.id);
      return true;
    }),
  }));
  return <section className="space-y-4">
    <div className="flex items-center gap-1 sm:gap-3">
      <h2 className="mr-auto text-base font-semibold sm:text-xl">{t('title')}</h2>
      <Button size="icon" className="h-8 w-8 shrink-0" variant="outline" disabled={!ready} onClick={() => setDate(shiftDay(date, -1))} aria-label={t('previous')}>←</Button>
      <span className="whitespace-nowrap text-sm">{dateLabel}</span>
      <Button size="icon" className="h-8 w-8 shrink-0" variant="outline" disabled={!ready || date >= shiftDay(today, 1)} onClick={() => setDate(shiftDay(date, 1))} aria-label={t('next')}>→</Button>
      <Button size="icon" className="h-8 w-8 shrink-0" variant="ghost" disabled={!ready} onClick={() => setDate(null)} aria-label={t('today')}><CalendarCheck className="h-4 w-4" /></Button>
    </div>
    {notice.date === date && notice.message && <p role="status">{notice.message}</p>}
    {logged && <WornOutfits key={date} outfits={wornOutfits} date={date} choices={sections.slice(0, 2).flatMap((section) => section.outfits)} disabled={!ready} onNotice={(message) => setNotice({ date, message })} />}
    {date >= today && <Button disabled={!ready || logged || generate.isPending || day.isLoading || day.isError} onClick={() => generate.mutate({ date, occasion: preferences?.default_occasion || 'casual' })}>{t('generate')}</Button>}
    {generate.isError && <p role="alert">{t('generateFailed')}</p>}
    {sections.map(({ title, query, outfits }) => <div key={title} className="space-y-3">
      <h3 className="font-semibold">{title}</h3>
      {query.isLoading && <p role="status">{common('loading')}</p>}
      {query.isError && <p role="alert">{t('failed')}</p>}
      {query.data?.pages[0]?.total === 0 && <p>{t('empty')}</p>}
      <div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(min(100%,18rem),1fr))]">
        {outfits.map((outfit) => <Card key={outfit.id}><CardContent className="p-3 space-y-3">
          <OutfitPreview key={outfit.id} outfitId={outfit.id} cardImageUrl={outfit.card_image_url} cardImageIsPreview={outfit.card_image_choice === 'preview'}><OutfitCard outfit={outfit} /></OutfitPreview>
          <WearActions outfit={outfit} date={date} disabled={!ready} dismissAction={
            !outfit.feedback?.worn_at && ['pending', 'sent', 'viewed'].includes(outfit.status)
              ? <Button size="sm" className="px-2 text-xs" variant="ghost" disabled={reject.isPending} onClick={() => reject.mutate(outfit.id)}>{t('dismiss')}</Button> : null
          } />
        </CardContent></Card>)}
      </div>
      {query.hasNextPage && <Button variant="outline" disabled={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>{common('loadMore')}</Button>}
    </div>)}
    {reject.isError && <p role="alert">{t('failed')}</p>}
  </section>;
}
