'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import { garmentImageUrl } from '@/lib/garment-image';
import { Check, Loader2, Search } from 'lucide-react';

import { Input } from '@/components/ui/input';
import { useItems } from '@/lib/hooks/use-items';
import { cn } from '@/lib/utils';
import { GRID_PAGE_SIZE } from '@/lib/pagination';
import type { Item } from '@/lib/types';
import { ITEM_ROLE } from '@/lib/generated/garment-vocabulary';
import { useTranslations } from 'next-intl';
import { useTypeLabel } from '@/lib/hooks/use-translated-constants';


const SLOT_ROLES = {
  all: [],
  tops: ['base_top', 'full_body'],
  layers: ['mid_layer'],
  jackets: ['outer_layer', 'suit'],
  bottoms: ['bottom', 'legwear'],
  underwear: ['underwear'],
  socks: ['socks'],
  shoes: ['footwear'],
  accessories: ['accessory', 'neckwear'],
} satisfies Record<string, string[]>;
type Slot = keyof typeof SLOT_ROLES;

interface ItemPickerProps {
  selectedIds: Set<string>;
  onToggle: (item: Item) => void;
  hideNeedsWash?: boolean;
  filterType?: string;
  emptyMessage?: string;
  heightClass?: string;
  outfitTypesOnly?: boolean;
  slotFilters?: boolean;
}

export function ItemPicker({
  selectedIds,
  onToggle,
  hideNeedsWash = true,
  filterType,
  emptyMessage,
  heightClass = 'h-[360px]',
  outfitTypesOnly = false,
  slotFilters = false,
}: ItemPickerProps) {
  const typeLabel = useTypeLabel();
  const t = useTranslations('outfits.itemPicker');
  const [slot, setSlot] = useState<Slot>('all');
  const lastLoadedData = useRef<unknown>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [page, setPage] = useState(1);
  const [accumulatedItems, setAccumulatedItems] = useState<Item[]>([]);
  const [accVersion, setAccVersion] = useState(0);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (searchQuery === debouncedSearch) return;
    const timer = setTimeout(() => {
      lastLoadedData.current = null;
      setDebouncedSearch(searchQuery);
      setPage(1);
      setAccumulatedItems([]);
      setAccVersion((v) => v + 1);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery, debouncedSearch]);

  const { data: itemsData, isLoading, isFetching } = useItems(
    {
      search: debouncedSearch || undefined,
      is_archived: false,
      type: filterType,
      needs_wash: hideNeedsWash ? false : undefined,
    },
    page,
    GRID_PAGE_SIZE
  );

  const hasMore = itemsData?.has_more ?? false;

  useEffect(() => {
    if (itemsData?.items) {
      if (page === 1) {
        setAccumulatedItems(itemsData.items);
      } else {
        setAccumulatedItems((prev) => {
          const existingIds = new Set(prev.map((i) => i.id));
          const newItems = itemsData.items.filter(
            (i) => !existingIds.has(i.id)
          );
          return [...prev, ...newItems];
        });
      }
    }
  }, [itemsData?.items, page, accVersion]);

  const matchesSlot = useCallback((item: Item) => {
    const role = ITEM_ROLE[(item.type ?? '').trim().toLowerCase()];
    return (!outfitTypesOnly || role !== 'underwear') &&
      (!slotFilters || slot === 'all' || (SLOT_ROLES[slot] as string[]).includes(role));
  }, [outfitTypesOnly, slotFilters, slot]);
  const items = (accumulatedItems.length > 0 ? accumulatedItems : itemsData?.items ?? [])
    .filter(matchesSlot);

  const loadMore = useCallback(() => {
    if (hasMore && !isFetching && itemsData && lastLoadedData.current !== itemsData) {
      lastLoadedData.current = itemsData;
      setPage((previousPage) => previousPage + 1);
    }
  }, [hasMore, isFetching, itemsData]);

  useEffect(() => {
    const accumulatedIds = new Set(accumulatedItems.map((item) => item.id));
    if (itemsData?.items.some((item) => !accumulatedIds.has(item.id))) return;
    const container = scrollContainerRef.current;
    const fillList = () => {
      if (slotFilters && container && container.scrollHeight <= container.clientHeight + 100) loadMore();
    };
    if (itemsData?.items.every((item) => !matchesSlot(item))) loadMore();
    fillList();
    const observer = new ResizeObserver(fillList);
    if (container) observer.observe(container);
    return () => observer.disconnect();
  }, [items.length, slot, slotFilters, outfitTypesOnly, itemsData, loadMore, matchesSlot, accumulatedItems]);

  const handleScroll = useCallback(
    (e: React.UIEvent<HTMLDivElement>) => {
      const target = e.target as HTMLDivElement;
      const nearBottom =
        target.scrollHeight - target.scrollTop <= target.clientHeight + 100;
      if (nearBottom) loadMore();
    },
    [loadMore]
  );

  return (
    <div className={cn("flex flex-col gap-2", slotFilters && "flex-1 min-h-0")}>
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder={t('search')}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="pl-9 h-9"
        />
      </div>

      {slotFilters && (
        <div className="flex flex-wrap gap-1" aria-label={t('slots.label')}>
          {(Object.keys(SLOT_ROLES) as Slot[]).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={slot === value}
              onClick={() => {
                setSlot(value);
                if (scrollContainerRef.current) scrollContainerRef.current.scrollTop = 0;
              }}
              className={cn(
                'rounded-full border px-3 py-1 text-xs',
                slot === value ? 'bg-primary text-primary-foreground border-primary' : 'hover:bg-muted'
              )}
            >
              {t(`slots.${value}`)}
            </button>
          ))}
        </div>
      )}

      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className={cn('overflow-y-auto py-2 -mx-1 px-1', heightClass)}
      >
        <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 gap-2">
          {items.map((item) => {
            const isSelected = selectedIds.has(item.id);
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onToggle(item)}
                className={cn(
                  'relative aspect-square rounded-lg overflow-hidden border-2 transition-all bg-muted',
                  isSelected
                    ? 'border-primary ring-2 ring-primary/20'
                    : 'border-border hover:border-muted-foreground/50'
                )}
              >
                {garmentImageUrl(item, 'card') ? (
                  <Image
                    src={garmentImageUrl(item, 'card')!}
                    alt={item.name || typeLabel(item.type)}
                    fill
                    className="object-contain"
                    sizes="(max-width: 640px) 33vw, 20vw"
                    loading="lazy"
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center bg-muted">
                    <span className="text-xs text-muted-foreground">
                      {typeLabel(item.type)}
                    </span>
                  </div>
                )}
                {isSelected && (
                  <div className="absolute inset-0 bg-primary/30 flex items-center justify-center">
                    <div className="rounded-full bg-primary p-1.5 shadow-lg">
                      <Check className="h-4 w-4 text-primary-foreground" />
                    </div>
                  </div>
                )}
                <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/70 to-transparent p-1.5">
                  <span className="text-[10px] sm:text-xs text-white font-medium truncate block">
                    {item.name ?? item.type}
                  </span>
                </div>
              </button>
            );
          })}
        </div>

        {isLoading && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        )}

        {isFetching && !isLoading && (
          <div className="flex items-center justify-center py-4">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground mr-2" />
            <span className="text-xs text-muted-foreground">
              {t('loadingMore')}
            </span>
          </div>
        )}

        {!isLoading && items.length === 0 && (
          <div className="text-center text-muted-foreground py-8">
            {debouncedSearch
              ? t('noMatch')
              : emptyMessage ?? t('noItems')}
          </div>
        )}

        {!isLoading && !hasMore && items.length > 0 && (
          <div className="text-center text-xs text-muted-foreground py-3">
            {t('showingAll', { count: items.length })}
          </div>
        )}
      </div>
    </div>
  );
}
