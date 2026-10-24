'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { useInspiration } from '@/lib/hooks/use-inspiration';

export default function InspirationPage() {
  const t = useTranslations('inspiration');
  const common = useTranslations('common');
  const { looks, create, remove } = useInspiration();
  const [file, setFile] = useState<File | null>(null);
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">{t('title')}</h1>
      <p className="text-muted-foreground">{t('description')}</p>
      <form className="flex flex-wrap items-end gap-4" onSubmit={(event) => {
        event.preventDefault();
        if (file) create.mutate(file);
      }}>
        <label className="space-y-2">
          <span className="block text-sm font-medium">{t('photo')}</span>
          <input type="file" accept="image/jpeg,image/png,image/webp" required
            disabled={create.isPending} onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              create.reset();
            }} />
        </label>
        <Button disabled={!file || create.isPending}>
          {create.isPending ? t('working') : t('create')}
        </Button>
      </form>
      {(create.isError || looks.isError || remove.isError) && <p role="alert">{t('error')}</p>}
      {create.isSuccess && <p role="status">{t('saved')}</p>}
      {looks.isPending && <p role="status">{common('loading')}</p>}
      {looks.data?.length === 0 && <p>{t('empty')}</p>}
      {looks.data?.map((look) => (
        <article key={look.id} className="space-y-4 rounded-xl border bg-card p-4">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <Link className="underline" href={`/dashboard/outfits/${look.id}`}>{t('open')}</Link>
            <Button variant="outline" disabled={remove.isPending}
              onClick={() => remove.mutate(look.id)}>{common('delete')}</Button>
          </div>
          <div className="grid gap-6 md:grid-cols-2">
            <img src={look.image_url} alt={t('photo')} className="max-h-96 w-full rounded-lg object-contain" />
            <div className="space-y-3">
              {look.slots.map((slot, index) => {
                const item = look.items.find((candidate) => candidate.id === slot.item_id);
                return <div key={index} className="flex items-center gap-3 rounded-lg border p-3">
                  {item && <img src={item.image_url} alt={item.name} className="h-20 w-20 rounded object-contain" />}
                  <div>
                    {item && <p className="font-medium">{item.name}</p>}
                    <p>{[slot.color, slot.type, slot.fit, slot.pattern].join(', ')}</p>
                    <p className={slot.no_match ? 'font-semibold text-destructive' : 'text-muted-foreground'}>
                      {slot.no_match ? t('noMatch') : t('strength', { value: Math.round(slot.match_strength * 100) })}
                    </p>
                  </div>
                </div>;
              })}
              {look.items.filter((item) => !look.slots.some((slot) => slot.item_id === item.id)).map((item) => (
                <div key={item.id} className="flex items-center gap-3 rounded-lg border p-3">
                  <img src={item.image_url} alt={item.name} className="h-20 w-20 rounded object-contain" />
                  <span>{t('baseLayer')}</span>
                </div>
              ))}
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}
