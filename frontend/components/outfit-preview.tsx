'use client';

import type { ReactNode } from 'react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { useOutfitPreview } from '@/lib/hooks/use-outfit-preview';

export function OutfitPreview(props: { outfitId: string; children?: ReactNode; detail?: boolean }) {
  return <PreviewContent key={props.outfitId} {...props} />;
}

function PreviewContent({ outfitId, children, detail }: { outfitId: string; children?: ReactNode; detail?: boolean }) {
  const t = useTranslations('outfits.preview');
  const { preview, pending, error, missingPhoto, draw } = useOutfitPreview(outfitId);
  return <div className={children && preview ? (detail ? "grid items-start gap-4 sm:grid-cols-2" : "grid grid-cols-2 gap-3 items-start") : "space-y-3"}>
    {children}
    <div className="space-y-2 min-w-0">
    <Button variant="outline" disabled={pending} onClick={() => draw()}>{pending ? t('drawing') : t('see')}</Button>
    {pending && <p role="status" className="text-sm text-muted-foreground">{t('drawing')}</p>}
    {missingPhoto && <Link className="block text-sm underline" href="/dashboard/settings#preview-photo">{t('addPhoto')}</Link>}
    {error && <p role="alert" className="text-sm text-destructive">{t('failed')}</p>}
    {preview && <>
      <a href={preview.image_url} target="_blank" rel="noopener noreferrer">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={preview.medium_url || preview.image_url} alt={t('see')} className="max-h-96 w-full rounded-lg object-contain bg-muted" />
      </a>
      {preview.stale && <div className="flex flex-wrap items-center gap-2 text-sm">
        <span>{t('changed')}</span>
        <Button size="sm" variant="outline" disabled={pending} onClick={() => draw(true)}>{t('redraw')}</Button>
      </div>}
    </>}
    </div>
  </div>;
}
