'use client';

import { useRef, useState } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { useInspiration } from '@/lib/hooks/use-inspiration';
import { ApiError } from '@/lib/api';
import { useColorLabel, useTypeLabel, useFitLabel, usePatternLabel } from '@/lib/hooks/use-translated-constants';

const ACCEPTED_INSPIRATION_INPUT = '.jpg,.jpeg,.png,.webp';

const errorKeys: Record<number, string> = {
  413: 'photoTooLarge', 415: 'invalidPhoto', 422: 'checkPhotoAndLocation',
  429: 'rateLimit', 503: 'unavailable',
};

export default function InspirationPage() {
  const t = useTranslations('inspiration');
  const common = useTranslations('common');
  const { looks, create, remove } = useInspiration();
  const [file, setFile] = useState<File | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const colorLabel = useColorLabel();
  const typeLabel = useTypeLabel();
  const fitLabel = useFitLabel();
  const patternLabel = usePatternLabel();
  const error = create.error || remove.error || looks.error;
  const requestErrorKey = error instanceof ApiError ? errorKeys[error.status] : undefined;
  const errorKey = validationError ?? (error ? requestErrorKey ?? 'error' : null);
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">{t('title')}</h1>
      <p className="text-muted-foreground">{t('description')}</p>
      <form className="flex flex-wrap items-end gap-4" onSubmit={(event) => {
        event.preventDefault();
        if (!file || create.isPending || remove.isPending || create.isSuccess) return;
        remove.reset();
        create.mutate(file, { onSuccess: () => {
          setFile(null);
          if (inputRef.current) inputRef.current.value = '';
        } });
      }}>
        <label className="space-y-2">
          <span className="block text-sm font-medium">{t('photo')}</span>
          <input ref={inputRef} type="file" accept={ACCEPTED_INSPIRATION_INPUT} required
            disabled={create.isPending || remove.isPending} onChange={(event) => {
              if (create.isPending || remove.isPending) return;
              const selected = event.target.files?.[0] ?? null;
              create.reset();
              remove.reset();
              setValidationError(null);
              setFile(null);
              if (!selected) return;
              const extension = `.${selected.name.split('.').pop()?.toLowerCase()}`;
              if (!ACCEPTED_INSPIRATION_INPUT.split(',').includes(extension)
                  || !['image/jpeg', 'image/png', 'image/webp'].includes(selected.type)) {
                setValidationError('invalidPhoto');
                return;
              }
              if (selected.size > 10 * 1024 * 1024) {
                setValidationError('photoTooLarge');
                return;
              }
              setFile(selected);
            }} />
        </label>
        <Button disabled={!file || create.isPending || remove.isPending || create.isSuccess}>
          {create.isPending ? t('working') : t('create')}
        </Button>
      </form>
      {errorKey && <p role="alert">{t(errorKey)}</p>}
      {create.isSuccess && !errorKey && <p role="status">{t('saved')}</p>}
      {looks.isPending && <p role="status">{common('loading')}</p>}
      {looks.data?.length === 0 && <p>{t('empty')}</p>}
      {looks.data?.map((look) => (
        <article key={look.id} className="space-y-4 rounded-xl border bg-card p-4">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <Link className="underline" href={`/dashboard/outfits/${look.id}`}>{t('open')}</Link>
            <Button variant="outline" disabled={remove.isPending || create.isPending}
              onClick={() => {
                if (create.isPending || remove.isPending) return;
                if (!confirm(t('deleteConfirm'))) return;
                create.reset();
                setValidationError(null);
                remove.mutate(look.id);
              }}>{common('delete')}</Button>
          </div>
          <div className="grid gap-6 md:grid-cols-2">
            <Image unoptimized width={800} height={800} src={look.image_url} alt={t('photoAlt')} className="max-h-96 w-full rounded-lg object-contain" />
            <div className="space-y-3">
              {look.slots.map((slot, index) => {
                const item = look.items.find((candidate) => candidate.id === slot.item_id);
                return <div key={index} className="flex items-center gap-3 rounded-lg border p-3">
                  {item && <Image unoptimized width={80} height={80} src={item.image_url} alt={item.name} className="h-20 w-20 rounded object-contain" />}
                  <div>
                    {item && <p className="font-medium">{item.name}</p>}
                    <p>{[colorLabel(slot.color), typeLabel(slot.type), fitLabel(slot.fit), patternLabel(slot.pattern)].join(', ')}</p>
                    <p className={slot.no_match ? 'font-semibold text-destructive' : 'text-muted-foreground'}>
                      {slot.no_match ? t('noMatch') : t('strength', { value: Math.round(slot.match_strength * 100) })}
                    </p>
                  </div>
                </div>;
              })}
              {look.items.filter((item) => !look.slots.some((slot) => slot.item_id === item.id)).map((item) => (
                <div key={item.id} className="flex items-center gap-3 rounded-lg border p-3">
                  <Image unoptimized width={80} height={80} src={item.image_url} alt={item.name} className="h-20 w-20 rounded object-contain" />
                  <div><p className="font-medium">{item.name}</p><span>{t('baseLayer')}</span></div>
                </div>
              ))}
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}
