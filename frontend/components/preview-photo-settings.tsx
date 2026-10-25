'use client';

import { useState } from 'react';
import { ACCEPTED_IMAGE_INPUT } from '@/lib/image-types';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from '@/components/ui/card';
import { usePreviewPhoto } from '@/lib/hooks/use-outfit-preview';

export function PreviewPhotoSettings() {
  const t = useTranslations('settings.previewPhoto');
  const { photo, loading, pending, error, save } = usePreviewPhoto();
  const [confirm, setConfirm] = useState(false);
  return <Card id="preview-photo">
    <CardHeader><CardTitle>{t('title')}</CardTitle><CardDescription>{t('guidance')}</CardDescription></CardHeader>
    <CardContent className="space-y-3">
      {photo && <a href={photo.image_url} target="_blank" rel="noopener noreferrer">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={photo.medium_url || photo.image_url} alt={t('title')} className="max-h-64 rounded-lg object-contain" />
      </a>}
      <label className="block space-y-2 text-sm font-medium">
        <span>{photo ? t('replace') : t('upload')}</span>
        <input type="file" accept={ACCEPTED_IMAGE_INPUT} disabled={loading || pending} className="block w-full text-sm" onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) { setConfirm(false); void save(file); }
        }} />
      </label>
      {photo && !confirm && <Button variant="outline" disabled={pending} onClick={() => setConfirm(true)}>{t('remove')}</Button>}
      {confirm && <div className="space-y-2">
        <p>{t('confirm')}</p>
        <div className="flex gap-2">
          <Button variant="destructive" disabled={pending} onClick={async () => { await save(); setConfirm(false); }}>{t('remove')}</Button>
          <Button variant="outline" disabled={pending} onClick={() => setConfirm(false)}>{t('cancel')}</Button>
        </div>
      </div>}
      {(loading || pending) && <p role="status">{t('loading')}</p>}
      {error && <p role="alert" className="text-destructive">{t('failed')}</p>}
    </CardContent>
  </Card>;
}
