'use client';

import { useEffect, useState } from 'react';
import Image from 'next/image';
import { useTranslations } from 'next-intl';
import { Camera, Loader2, Sparkles } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { ACCEPTED_IMAGE_INPUT } from '@/lib/image-types';
import { ApiError } from '@/lib/api';
import { useSelfieRecognition } from '@/lib/hooks/use-selfie';
import { useTypeLabel } from '@/lib/hooks/use-translated-constants';

export default function SelfiePage() {
  const t = useTranslations('selfie');
  const typeLabel = useTypeLabel();
  const recognise = useSelfieRecognition();
  const [preview, setPreview] = useState<string | null>(null);
  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  const choose = (next: File | null) => {
    setPreview(next ? URL.createObjectURL(next) : null);
    recognise.reset();
    if (next) recognise.mutate(next);
  };

  const errorKeys: Record<number, string> = {
    400: 'invalidPhoto', 413: 'photoTooLarge', 429: 'rateLimit', 503: 'aiOff',
  };
  const errorText = recognise.error
    ? t(recognise.error instanceof ApiError ? (errorKeys[recognise.error.status] ?? 'aiError') : 'aiError')
    : null;
  const matches = recognise.data?.matches ?? [];

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
              disabled={recognise.isPending}
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
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
