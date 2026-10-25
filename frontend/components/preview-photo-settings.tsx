'use client';

import { useState } from 'react';
import { ACCEPTED_IMAGE_INPUT } from '@/lib/image-types';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from '@/components/ui/card';
import { previewAngles, type PreviewAngle, usePreviewPhoto } from '@/lib/hooks/use-outfit-preview';

export function PreviewPhotoSettings() {
  const t = useTranslations('settings.previewPhoto');
  const common = useTranslations('common');
  const { photos, loading, pending, error, save } = usePreviewPhoto();
  const [confirm, setConfirm] = useState<PreviewAngle | null>(null);
  return <Card id="preview-photo">
    <CardHeader><CardTitle>{t('title')}</CardTitle><CardDescription>{t('guidance')}</CardDescription></CardHeader>
    <CardContent className="space-y-3">
      <div className="grid gap-6 sm:grid-cols-3">
        {previewAngles.map((angle) => {
          const photo = photos?.[angle];
          const label = t(photo ? 'replaceAngle' : 'uploadAngle', { angle: t(angle) });
          return <fieldset key={angle} className="min-w-0 space-y-3">
            <legend className="font-medium">{t(angle)}</legend>
            {photo && <a href={photo.image_url} target="_blank" rel="noopener noreferrer">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={photo.medium_url || photo.image_url} alt={t(angle === 'front' ? 'title' : angle)} className="max-h-64 rounded-lg object-contain" />
            </a>}
            <label className="block space-y-2 text-sm font-medium">
              <span>{label}</span>
              <input type="file" accept={ACCEPTED_IMAGE_INPUT} disabled={loading || pending}
                aria-label={label}
                className="block w-full text-sm" onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = '';
                  if (file) { setConfirm(null); void save(file, angle); }
                }} />
            </label>
            {photo && confirm !== angle && <Button variant="outline" disabled={pending} onClick={() => setConfirm(angle)}>{common('remove')}</Button>}
            {confirm === angle && <div className="space-y-2">
              <p>{t(angle === 'front' ? 'confirm' : 'confirmAngle')}</p>
              <div className="flex gap-2">
                <Button variant="destructive" disabled={pending} onClick={async () => { if (await save(undefined, angle)) setConfirm(null); }}>{common('remove')}</Button>
                <Button variant="outline" disabled={pending} onClick={() => setConfirm(null)}>{common('cancel')}</Button>
              </div>
            </div>}
          </fieldset>;
        })}
      </div>
      {(loading || pending) && <p role="status">{common('loading')}</p>}
      {error && <p role="alert" className="text-destructive">{t('failed')}</p>}
    </CardContent>
  </Card>;
}
