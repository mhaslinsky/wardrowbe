'use client';

import { useEffect, useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { Camera, Loader2, Sparkles } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { ACCEPTED_IMAGE_INPUT } from '@/lib/image-types';
import { ApiError } from '@/lib/api';
import { useSelfieRecognition, useConfirmSelfie } from '@/lib/hooks/use-selfie';
import { useItems } from '@/lib/hooks/use-items';
import { ITEM_ROLE } from '@/lib/studio/canonical-order';
import { Button } from '@/components/ui/button';
import type { Item } from '@/lib/types';
import { useTypeLabel } from '@/lib/hooks/use-translated-constants';

export default function SelfiePage() {
  const t = useTranslations('selfie');
  const typeLabel = useTypeLabel();
  const recognise = useSelfieRecognition();
  const confirm = useConfirmSelfie();
  const [selected, setSelected] = useState<Item[]>([]);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [wardrobePage, setWardrobePage] = useState(1);
  const [swapId, setSwapId] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const wardrobe = useItems({ is_archived: false }, wardrobePage);
  const locked = confirm.isPending || confirm.isSuccess;
  const changeSelection = (items: Item[]) => {
    setSelected(items);
    setRequestId(crypto.randomUUID());
    confirm.reset();
  };
  const [preview, setPreview] = useState<string | null>(null);
  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  const choose = (next: File | null) => {
    setPreview(next ? URL.createObjectURL(next) : null);
    recognise.reset();
    confirm.reset();
    setSelected([]);
    setRequestId(null);
    setPickerOpen(false);
    setSwapId(null);
    if (next) recognise.mutate(next, {
      onSuccess: (result) => changeSelection(result.matches),
    });
  };

  const errorKeys: Record<number, string> = {
    400: 'invalidPhoto', 413: 'photoTooLarge', 429: 'rateLimit', 503: 'aiOff',
  };
  const errorText = recognise.error
    ? t(recognise.error instanceof ApiError ? (errorKeys[recognise.error.status] ?? 'aiError') : 'aiError')
    : null;
  const matches = selected;
  const confirmDetail = confirm.error instanceof ApiError
    ? (confirm.error.data as { detail?: { error_code?: string; existing_ids?: string[] } })?.detail
    : undefined;
  const confirmCode = confirmDetail?.error_code;
  const loggedId = confirm.data?.id ?? (confirmCode === 'DAY_LOGGED' ? confirmDetail?.existing_ids?.[0] : undefined);
  const swapItem = selected.find((item) => item.id === swapId);
  const choices = (wardrobe.data?.items ?? []).filter((item) =>
    item.status === 'ready' && !selected.some((chosen) => chosen.id === item.id) &&
    (!swapItem || (ITEM_ROLE[item.type] ?? item.type) === (ITEM_ROLE[swapItem.type] ?? swapItem.type))
  );

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <Camera className="h-6 w-6" />
          {t('title')}
        </h1>
        <p className="text-muted-foreground">{t('subtitle')}</p>
      </div>

      <Card>
        <CardContent className="space-y-4 pt-6">
          <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground hover:bg-accent">
            {preview ? (
              // eslint-disable-next-line @next/next/no-img-element -- a local blob preview
              <img src={preview} alt={t('photoAlt')} className="max-h-80 rounded-md object-contain" />
            ) : (
              <>
                <Camera className="h-8 w-8" />
                <span className="font-medium text-foreground">{t('pick')}</span>
                <span>{t('pickHint')}</span>
              </>
            )}
            <input
              type="file"
              disabled={recognise.isPending || confirm.isPending}
              aria-label={t('pick')}
              accept={ACCEPTED_IMAGE_INPUT}
              className="hidden"
              onChange={(event) => {
                choose(event.target.files?.[0] ?? null);
                event.target.value = '';
              }}
            />
          </label>

          {recognise.isPending && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t('working')}
            </div>
          )}
          {errorText && <p className="text-sm text-destructive">{errorText}</p>}
        </CardContent>
      </Card>

      {recognise.isSuccess && (
        <Card>
          <CardContent className="space-y-4 pt-6">
            <div className="flex items-center gap-2 font-medium">
              <Sparkles className="h-4 w-4" />
              {matches.length > 0 ? t('found', { count: matches.length }) : t('noneFound')}
            </div>
            {matches.length > 0 && (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {matches.map((item) => {
                  return (
                    <div
                      key={item.id}
                      className="rounded-lg border p-2 text-left"
                    >
                      <div className="relative mb-2 aspect-square overflow-hidden rounded-md bg-muted">
                        {item.thumbnail_url && (
                          <Image
                            src={item.thumbnail_url}
                            alt={item.name || typeLabel(item.type)}
                            fill
                            className="object-contain"
                            sizes="160px"
                          />
                        )}
                      </div>
                      <div className="flex items-center gap-2 text-sm">
                        <span className="truncate">{item.name || typeLabel(item.type)}</span>
                      </div>
                      <Button variant="ghost" disabled={locked} onClick={() => changeSelection(selected.filter((chosen) => chosen.id !== item.id))}>
                        {t('remove')}
                      </Button>
                      <Button variant="ghost" disabled={locked} onClick={() => { setSwapId(item.id); setWardrobePage(1); setPickerOpen(true); }}>
                        {t('swap')}
                      </Button>
                    </div>
                  );
                })}
              </div>
            )}
            <Button variant="outline" disabled={locked} onClick={() => { setSwapId(null); setWardrobePage(1); setPickerOpen(true); }}>{t('add')}</Button>
            {pickerOpen && !locked && (
              <div className="space-y-2" role="group" aria-label={t('wardrobe')}>
                {wardrobe.isLoading && <p>{t('loadingWardrobe')}</p>}
                {wardrobe.isError && <p role="alert">{t('wardrobeError')}</p>}
                {choices.map((item) => <Button key={item.id} variant="outline" onClick={() => {
                  changeSelection(swapId ? selected.map((chosen) => chosen.id === swapId ? item : chosen) : [...selected, item]);
                  setPickerOpen(false);
                  setSwapId(null);
                }}>{item.name || typeLabel(item.type)}</Button>)}
                {!wardrobe.isLoading && !wardrobe.isError && choices.length === 0 && <p>{t('noChoices')}</p>}
                <div className="flex gap-2">
                  <Button variant="ghost" disabled={wardrobePage === 1 || wardrobe.isFetching} onClick={() => setWardrobePage(wardrobePage - 1)}>{t('previous')}</Button>
                  <Button variant="ghost" disabled={!wardrobe.data?.has_more || wardrobe.isFetching} onClick={() => setWardrobePage(wardrobePage + 1)}>{t('next')}</Button>
                  <Button variant="ghost" onClick={() => { setPickerOpen(false); setSwapId(null); }}>{t('cancel')}</Button>
                </div>
              </div>
            )}
            <Button disabled={locked || !selected.length || !requestId} onClick={() => {
              if (requestId) confirm.mutate({ request_id: requestId, item_ids: selected.map((item) => item.id) });
            }}>{confirm.isPending ? t('logging') : t('confirm')}</Button>
            {confirm.isSuccess && <p role="status">{t('logged')}</p>}
            {confirm.isError && <p role="alert">{t(confirmCode === 'DAY_LOGGED' ? 'dayLogged' : confirmCode === 'REQUEST_REUSED' ? 'requestReused' : confirmCode === 'CONFIRM_RESULT_DELETED' ? 'resultDeleted' : 'confirmError')}</p>}
            {loggedId && <Link href={`/dashboard/outfits/${loggedId}`} className="block underline">{t('viewOutfit')}</Link>}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
